//! Derives for `shmem-pod` shared-memory capability traits.
//!
//! Derive exactly one storage tier (`PodValue` or `FixedAddressPodValue`) and
//! optionally derive `PodSync`. Generic structs are rejected because stable
//! Rust cannot eagerly prove that every instantiation has no destructor.

use proc_macro::TokenStream;
use proc_macro_crate::{FoundCrate, crate_name};
use proc_macro2::{Span, TokenStream as TokenStream2};
use quote::quote;
use syn::{
    Data, DeriveInput, Fields, Generics, Index, Member, Type, parse_macro_input, parse_quote,
};

mod pod;

#[proc_macro_attribute]
/// Declares a stable, scalar-only native method table for an executable pod.
///
/// Apply this macro to an `unsafe extern "C"` declaration block. Every method
/// needs `#[pod_method(id = N, symbol = "exported_name")]`. The macro removes
/// the foreign declarations and generates a sorted descriptor plus typed
/// bindings. It rejects generics, variadics, asynchronous functions, duplicate
/// IDs or symbols, and types outside the explicitly supported C ABI shapes.
pub fn pod(arguments: TokenStream, input: TokenStream) -> TokenStream {
    pod::expand(
        arguments.into(),
        parse_macro_input!(input as syn::ItemForeignMod),
    )
    .unwrap_or_else(syn::Error::into_compile_error)
    .into()
}

#[proc_macro_derive(PodValue)]
/// Derives the address-independent structural storage tier for a concrete struct.
///
/// Every field must implement `shmem_pod::PodValue`. The macro also implements
/// `FixedAddressPodValue` and computes a fingerprint from the type identity,
/// size, alignment, field names, compiler-selected offsets, and transitive
/// fingerprints. `#[repr(C)]` is not required; compatibility is for the exact
/// authenticated build, not a stable Rust ABI.
///
/// Enums, unions, generic structs, fields needing drop, references, raw
/// pointers, standard owning collections, and fields without `PodValue` are
/// rejected. Materialize `T::FINGERPRINT` in an image descriptor; this also
/// forces evaluation of the generated no-drop assertion.
///
/// This derive certifies stored field representation only. It does not inspect
/// methods, unsafe blocks, byte validity, or scalar semantics such as casting a
/// `usize` to a pointer.
pub fn derive_pod_value(input: TokenStream) -> TokenStream {
    derive_storage(
        parse_macro_input!(input as DeriveInput),
        StorageTier::Relocatable,
    )
    .unwrap_or_else(syn::Error::into_compile_error)
    .into()
}

#[proc_macro_derive(FixedAddressPodValue)]
/// Derives the same-virtual-address structural storage tier for a concrete struct.
///
/// Every field must implement `shmem_pod::FixedAddressPodValue`. The generated
/// fingerprint covers the exact compiler-selected layout and transitive field
/// fingerprints, so user structs may retain default Rust representation.
/// Generics, enums, unions, destructors, and fields without the capability are
/// rejected.
///
/// This derive is not permission to use arbitrary pointers. Absolute-address
/// fields need an audited wrapper with an unsafe constructor, and every process
/// must obey that wrapper's exact mapping contract. Scalar meanings, methods,
/// and external resources remain outside this structural capability.
pub fn derive_fixed_address_pod_value(input: TokenStream) -> TokenStream {
    derive_storage(
        parse_macro_input!(input as DeriveInput),
        StorageTier::FixedAddress,
    )
    .unwrap_or_else(syn::Error::into_compile_error)
    .into()
}

#[proc_macro_derive(PodSync)]
/// Derives structural process-shared access for a concrete stored struct.
///
/// The struct must already implement `FixedAddressPodValue`, every field must
/// implement `PodSync`, and the resulting type must satisfy Rust's `Sync`
/// supertrait. Derive `PodValue` or `FixedAddressPodValue` alongside this macro.
///
/// This marker covers ordinary typed field access. It does not audit arbitrary
/// methods or unsafe code, make raw writes safe, or add owner-death recovery.
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
    match crate_name("shmem-pod") {
        Ok(FoundCrate::Itself) => quote!(::shmem_pod),
        Ok(FoundCrate::Name(name)) => {
            let ident = syn::Ident::new(&name, Span::call_site());
            quote!(::#ident)
        }
        Err(_) => quote!(::shmem_pod),
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
    reject_generics(&input)?;
    let fields = struct_fields(&input)?;
    let pod = pod_types_path();
    let name = &input.ident;
    let (field_bound, domain) = match tier {
        StorageTier::Relocatable => (
            quote!(#pod::PodValue),
            b"shmem-pod-derived-value-v1".as_slice(),
        ),
        StorageTier::FixedAddress => (
            quote!(#pod::FixedAddressPodValue),
            b"shmem-pod-derived-fixed-address-v1".as_slice(),
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
    reject_generics(&input)?;
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

fn reject_generics(input: &DeriveInput) -> syn::Result<()> {
    if input.generics.params.is_empty() {
        Ok(())
    } else {
        Err(syn::Error::new_spanned(
            &input.generics,
            "generic pod capability derives are not supported: stable Rust cannot eagerly prove that every instantiation has no destructor; use a concrete wrapper or a manually audited unsafe impl",
        ))
    }
}
