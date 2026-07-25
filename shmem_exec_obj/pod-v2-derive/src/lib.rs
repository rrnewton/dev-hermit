//! Derives for `pod-v2-types` shared-memory capability traits.
//!
//! Derive exactly one storage tier (`PodValue` or `FixedAddressPodValue`) and
//! optionally derive `PodSync`. Generic types perform their no-drop check when
//! their mandatory layout fingerprint is instantiated; image descriptors must
//! always materialize that fingerprint.

use proc_macro::TokenStream;
use proc_macro_crate::{FoundCrate, crate_name};
use proc_macro2::{Span, TokenStream as TokenStream2};
use quote::quote;
use syn::{
    Data, DeriveInput, Fields, Generics, Index, Member, Type, parse_macro_input, parse_quote,
};

#[proc_macro_derive(PodValue)]
/// Derives the address-independent storage tier for a struct.
pub fn derive_pod_value(input: TokenStream) -> TokenStream {
    derive_storage(
        parse_macro_input!(input as DeriveInput),
        StorageTier::Relocatable,
    )
    .unwrap_or_else(syn::Error::into_compile_error)
    .into()
}

#[proc_macro_derive(FixedAddressPodValue)]
/// Derives the same-virtual-address storage tier for a struct.
pub fn derive_fixed_address_pod_value(input: TokenStream) -> TokenStream {
    derive_storage(
        parse_macro_input!(input as DeriveInput),
        StorageTier::FixedAddress,
    )
    .unwrap_or_else(syn::Error::into_compile_error)
    .into()
}

#[proc_macro_derive(PodSync)]
/// Derives process-shared access capability for a struct with a storage tier.
pub fn derive_pod_sync(input: TokenStream) -> TokenStream {
    derive_sync(parse_macro_input!(input as DeriveInput))
        .unwrap_or_else(syn::Error::into_compile_error)
        .into()
}

#[derive(Clone, Copy)]
enum StorageTier {
    Relocatable,
    FixedAddress,
}

struct FieldInfo {
    member: Member,
    name: String,
    ty: Type,
}

fn pod_types_path() -> TokenStream2 {
    match crate_name("pod-v2-types") {
        Ok(FoundCrate::Itself) => quote!(crate),
        Ok(FoundCrate::Name(name)) => {
            let ident = syn::Ident::new(&name, Span::call_site());
            quote!(::#ident)
        }
        Err(_) => quote!(::pod_v2_types),
    }
}

fn struct_fields(input: &DeriveInput) -> syn::Result<Vec<FieldInfo>> {
    let data = match &input.data {
        Data::Struct(data) => data,
        Data::Enum(_) => {
            return Err(syn::Error::new_spanned(
                input,
                "pod capability derives support structs only; encode enums as a checked integer tag",
            ));
        }
        Data::Union(_) => {
            return Err(syn::Error::new_spanned(
                input,
                "pod capability derives do not support unions",
            ));
        }
    };

    Ok(match &data.fields {
        Fields::Named(fields) => fields
            .named
            .iter()
            .map(|field| {
                let ident = field.ident.clone().expect("named field");
                FieldInfo {
                    member: Member::Named(ident.clone()),
                    name: ident.to_string(),
                    ty: field.ty.clone(),
                }
            })
            .collect(),
        Fields::Unnamed(fields) => fields
            .unnamed
            .iter()
            .enumerate()
            .map(|(position, field)| FieldInfo {
                member: Member::Unnamed(Index::from(position)),
                name: position.to_string(),
                ty: field.ty.clone(),
            })
            .collect(),
        Fields::Unit => Vec::new(),
    })
}

fn bounded_generics(input: &DeriveInput, fields: &[FieldInfo], bound: TokenStream2) -> Generics {
    let mut generics = input.generics.clone();
    for parameter in generics.type_params_mut() {
        parameter.bounds.push(parse_quote!('static));
    }
    let where_clause = generics.make_where_clause();
    for field in fields {
        let ty = &field.ty;
        where_clause.predicates.push(parse_quote!(#ty: #bound));
    }
    generics
}

fn derive_storage(input: DeriveInput, tier: StorageTier) -> syn::Result<TokenStream2> {
    let fields = struct_fields(&input)?;
    let pod = pod_types_path();
    let name = &input.ident;
    let (field_bound, domain) = match tier {
        StorageTier::Relocatable => (quote!(#pod::PodValue), b"pod-v2-derived-value".as_slice()),
        StorageTier::FixedAddress => (
            quote!(#pod::FixedAddressPodValue),
            b"pod-v2-derived-fixed-address".as_slice(),
        ),
    };
    let domain = syn::LitByteStr::new(domain, Span::call_site());
    let field_count = fields.len();
    let generics = bounded_generics(&input, &fields, field_bound);
    let (impl_generics, ty_generics, where_clause) = generics.split_for_impl();

    let field_mixers = fields.iter().map(|field| {
        let member = &field.member;
        let field_name = syn::LitByteStr::new(field.name.as_bytes(), Span::call_site());
        let ty = &field.ty;
        quote! {
            state = #pod::__private::mix_bytes(state, #field_name);
            state = #pod::__private::mix_usize(
                state,
                ::core::mem::offset_of!(Self, #member),
            );
            state = #pod::__private::mix_usize(state, ::core::mem::size_of::<#ty>());
            state = #pod::__private::mix_usize(state, ::core::mem::align_of::<#ty>());
            state = #pod::__private::mix_u128(
                state,
                <#ty as #pod::FixedAddressPodValue>::FINGERPRINT,
            );
        }
    });

    let eager_no_drop = if input.generics.params.is_empty() {
        quote! {
            const _: () = {
                assert!(
                    !::core::mem::needs_drop::<#name>(),
                    concat!(stringify!(#name), " cannot implement a pod value capability because it needs drop"),
                );
            };
        }
    } else {
        TokenStream2::new()
    };

    let fixed_impl = quote! {
        unsafe impl #impl_generics #pod::FixedAddressPodValue for #name #ty_generics
        #where_clause
        {
            const FINGERPRINT: u128 = {
                assert!(
                    !::core::mem::needs_drop::<Self>(),
                    "pod values must not need drop",
                );
                let mut state = #pod::__private::mix_bytes(
                    #pod::__private::FINGERPRINT_SEED,
                    #domain,
                );
                state = #pod::__private::mix_bytes(
                    state,
                    concat!(module_path!(), "::", stringify!(#name)).as_bytes(),
                );
                state = #pod::__private::mix_usize(state, ::core::mem::size_of::<Self>());
                state = #pod::__private::mix_usize(state, ::core::mem::align_of::<Self>());
                state = #pod::__private::mix_usize(state, #field_count);
                #(#field_mixers)*
                #pod::__private::finish(state)
            };
        }
    };

    let tier_impl = match tier {
        StorageTier::Relocatable => quote! {
            unsafe impl #impl_generics #pod::PodValue for #name #ty_generics
            #where_clause
            {}
        },
        StorageTier::FixedAddress => TokenStream2::new(),
    };

    Ok(quote! {
        #eager_no_drop
        #fixed_impl
        #tier_impl
    })
}

fn derive_sync(input: DeriveInput) -> syn::Result<TokenStream2> {
    let fields = struct_fields(&input)?;
    let pod = pod_types_path();
    let name = &input.ident;
    let mut generics = bounded_generics(&input, &fields, quote!(#pod::PodSync));
    let (_, original_ty_generics, _) = input.generics.split_for_impl();
    generics
        .make_where_clause()
        .predicates
        .push(parse_quote!(#name #original_ty_generics: #pod::FixedAddressPodValue));
    let (impl_generics, ty_generics, where_clause) = generics.split_for_impl();

    Ok(quote! {
        unsafe impl #impl_generics #pod::PodSync for #name #ty_generics
        #where_clause
        {}
    })
}
