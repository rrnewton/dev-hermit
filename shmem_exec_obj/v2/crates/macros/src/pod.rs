use proc_macro2::{Span, TokenStream};
use quote::{format_ident, quote};
use std::collections::{BTreeMap, BTreeSet};
use syn::parse::{Parse, ParseStream};
use syn::{
    Abi, Attribute, ForeignItem, ForeignItemFn, Ident, ItemForeignMod, LitInt, LitStr, ReturnType,
    Token, Type, TypePath, Visibility,
};

struct PodArguments {
    namespace: LitStr,
    bindings: Ident,
    descriptor: Ident,
}

impl Parse for PodArguments {
    fn parse(input: ParseStream<'_>) -> syn::Result<Self> {
        let mut namespace = None;
        let mut bindings = None;
        let mut descriptor = None;
        while !input.is_empty() {
            let name: Ident = input.parse()?;
            input.parse::<Token![=]>()?;
            match name.to_string().as_str() {
                "namespace" => namespace = Some(input.parse()?),
                "bindings" => bindings = Some(input.parse()?),
                "descriptor" => descriptor = Some(input.parse()?),
                _ => {
                    return Err(syn::Error::new_spanned(
                        name,
                        "unknown #[pod] option; expected namespace, bindings, or descriptor",
                    ));
                }
            }
            if input.is_empty() {
                break;
            }
            input.parse::<Token![,]>()?;
        }
        Ok(Self {
            namespace: namespace
                .ok_or_else(|| input.error("#[pod] requires namespace = \"...\""))?,
            bindings: bindings.ok_or_else(|| input.error("#[pod] requires bindings = TypeName"))?,
            descriptor: descriptor
                .ok_or_else(|| input.error("#[pod] requires descriptor = CONST_NAME"))?,
        })
    }
}

#[derive(Clone, Copy)]
enum Signature {
    NoArgsU64,
    StateU64Status,
    StateU64U64Status,
    StateU64OutU64Status,
    StateU64,
}

impl Signature {
    fn value(self) -> u16 {
        match self {
            Self::NoArgsU64 => 1,
            Self::StateU64Status => 2,
            Self::StateU64U64Status => 3,
            Self::StateU64OutU64Status => 4,
            Self::StateU64 => 5,
        }
    }

    fn tokens(self, pod: &TokenStream) -> TokenStream {
        match self {
            Self::NoArgsU64 => quote!(#pod::pod_api::MethodSignature::NoArgsU64),
            Self::StateU64Status => quote!(#pod::pod_api::MethodSignature::StateU64Status),
            Self::StateU64U64Status => {
                quote!(#pod::pod_api::MethodSignature::StateU64U64Status)
            }
            Self::StateU64OutU64Status => {
                quote!(#pod::pod_api::MethodSignature::StateU64OutU64Status)
            }
            Self::StateU64 => quote!(#pod::pod_api::MethodSignature::StateU64),
        }
    }
}

struct Method {
    rust_name: Ident,
    visibility: Visibility,
    id: u32,
    symbol: LitStr,
    signature: Signature,
    function_type: TokenStream,
}

pub fn expand(arguments: TokenStream, input: ItemForeignMod) -> syn::Result<TokenStream> {
    let arguments = syn::parse2::<PodArguments>(arguments)?;
    require_c_abi(&input.abi)?;
    if input.unsafety.is_none() {
        return Err(syn::Error::new_spanned(
            &input,
            "#[pod] requires an unsafe extern \"C\" declaration block",
        ));
    }

    let pod = super::pod_types_path();
    let mut methods = Vec::new();
    for item in input.items {
        let ForeignItem::Fn(function) = item else {
            return Err(syn::Error::new_spanned(
                item,
                "#[pod] blocks may contain function declarations only",
            ));
        };
        methods.push(parse_method(function)?);
    }
    if methods.is_empty() {
        return Err(syn::Error::new_spanned(
            input.abi,
            "#[pod] requires at least one method",
        ));
    }

    let mut ids = BTreeMap::new();
    let mut symbols = BTreeSet::new();
    for method in &methods {
        if let Some(previous) = ids.insert(method.id, method.rust_name.clone()) {
            return Err(syn::Error::new_spanned(
                &method.rust_name,
                format!(
                    "duplicate pod method ID {} (already used by {})",
                    method.id, previous
                ),
            ));
        }
        if !symbols.insert(method.symbol.value()) {
            return Err(syn::Error::new_spanned(
                &method.symbol,
                "duplicate pod export symbol",
            ));
        }
    }
    methods.sort_by_key(|method| method.id);

    let namespace = arguments.namespace;
    let bindings = arguments.bindings;
    let descriptor = arguments.descriptor;
    let methods_ident = format_ident!("__{}_METHODS", descriptor);
    let method_count = methods.len();

    let specifications = methods.iter().map(|method| {
        let id = method.id;
        let signature = method.signature.tokens(&pod);
        let symbol = &method.symbol;
        quote! {
            #pod::pod_api::MethodSpec {
                id: #id,
                signature: #signature,
                symbol: #symbol,
            }
        }
    });
    let fingerprint_mixers = methods.iter().map(|method| {
        let id = method.id;
        let signature = method.signature.value();
        quote! {
            state = #pod::pod_api::__private::mix_u128(state, #id as u128);
            state = #pod::pod_api::__private::mix_u128(state, #signature as u128);
        }
    });
    let fields = methods.iter().map(|method| {
        let visibility = &method.visibility;
        let name = &method.rust_name;
        let function_type = &method.function_type;
        quote! {
            #[doc = concat!("Typed entry for pod method `", stringify!(#name), "`.")]
            #visibility #name: #function_type
        }
    });
    let bind_fields = methods.iter().map(|method| {
        let name = &method.rust_name;
        let id = method.id;
        let signature = method.signature.tokens(&pod);
        let function_type = &method.function_type;
        quote! {
            #name: {
                let entry = resolver.resolve(#id, #signature)?;
                // SAFETY: MethodResolver's unsafe contract binds this address
                // to the exact function signature requested above.
                unsafe { ::core::mem::transmute::<*mut (), #function_type>(entry.as_ptr()) }
            }
        }
    });

    Ok(quote! {
        #[doc(hidden)]
        static #methods_ident: [#pod::pod_api::MethodSpec; #method_count] = [
            #(#specifications),*
        ];

        #[doc = concat!("Generated descriptor for pod API `", #namespace, "`.")]
        pub const #descriptor: #pod::pod_api::PodApiDescriptor = {
            let mut state = #pod::pod_api::__private::mix_bytes(
                #pod::pod_api::__private::API_FINGERPRINT_SEED,
                b"shmem-pod-api-v1",
            );
            state = #pod::pod_api::__private::mix_bytes(state, #namespace.as_bytes());
            state = #pod::pod_api::__private::mix_u128(state, #method_count as u128);
            #(#fingerprint_mixers)*
            #pod::pod_api::PodApiDescriptor {
                namespace: #namespace,
                fingerprint: #pod::pod_api::__private::finish(state),
                methods: &#methods_ident,
            }
        };

        #[doc = concat!("Typed entries for pod API `", #namespace, "`.")]
        #[derive(Clone, Copy)]
        pub struct #bindings {
            #(#fields),*
        }

        impl #bindings {
            /// Resolves and type-checks every required native entry.
            ///
            /// # Safety
            ///
            /// The returned bindings must not outlive the executable mapping
            /// which backs `resolver`. The resolver's unsafe implementation
            /// must authenticate code and uphold every declared C ABI.
            pub unsafe fn bind<R: #pod::pod_api::MethodResolver + ?Sized>(
                resolver: &R,
            ) -> ::core::result::Result<Self, #pod::pod_api::BindError> {
                Ok(Self {
                    #(#bind_fields),*
                })
            }
        }
    })
}

fn require_c_abi(abi: &Abi) -> syn::Result<()> {
    if abi.name.as_ref().map(LitStr::value).as_deref() == Some("C") {
        Ok(())
    } else {
        Err(syn::Error::new_spanned(
            abi,
            "#[pod] supports extern \"C\" only",
        ))
    }
}

fn parse_method(function: ForeignItemFn) -> syn::Result<Method> {
    if function.sig.asyncness.is_some() {
        return Err(syn::Error::new_spanned(
            function.sig.asyncness,
            "pod methods cannot be async",
        ));
    }
    if function.sig.constness.is_some() {
        return Err(syn::Error::new_spanned(
            function.sig.constness,
            "pod methods cannot be const",
        ));
    }
    if !function.sig.generics.params.is_empty() || function.sig.generics.where_clause.is_some() {
        return Err(syn::Error::new_spanned(
            function.sig.generics,
            "pod methods cannot be generic",
        ));
    }
    if function.sig.variadic.is_some() {
        return Err(syn::Error::new_spanned(
            function.sig.variadic,
            "pod methods cannot be variadic",
        ));
    }

    let (id, symbol) = parse_method_attribute(&function.attrs)?;
    let signature = classify_signature(&function)?;
    let inputs = function.sig.inputs.iter().map(|argument| match argument {
        syn::FnArg::Typed(argument) => &*argument.ty,
        syn::FnArg::Receiver(_) => unreachable!("foreign functions cannot have receivers"),
    });
    let output = &function.sig.output;
    let function_type = quote!(unsafe extern "C" fn(#(#inputs),*) #output);
    Ok(Method {
        rust_name: function.sig.ident,
        visibility: function.vis,
        id,
        symbol,
        signature,
        function_type,
    })
}

fn parse_method_attribute(attributes: &[Attribute]) -> syn::Result<(u32, LitStr)> {
    let mut id = None;
    let mut symbol = None;
    let mut found = false;
    for attribute in attributes {
        if attribute.path().is_ident("doc") {
            continue;
        }
        if !attribute.path().is_ident("pod_method") {
            return Err(syn::Error::new_spanned(
                attribute,
                "unsupported pod method attribute",
            ));
        }
        if found {
            return Err(syn::Error::new_spanned(
                attribute,
                "duplicate #[pod_method] attribute",
            ));
        }
        found = true;
        attribute.parse_nested_meta(|meta| {
            if meta.path.is_ident("id") {
                let literal: LitInt = meta.value()?.parse()?;
                let value = literal.base10_parse::<u32>()?;
                if value == 0 {
                    return Err(meta.error("pod method ID zero is reserved"));
                }
                id = Some(value);
                Ok(())
            } else if meta.path.is_ident("symbol") {
                let literal: LitStr = meta.value()?.parse()?;
                if literal.value().is_empty() || literal.value().as_bytes().contains(&0) {
                    return Err(meta.error("pod method symbol must be a nonempty C string"));
                }
                symbol = Some(literal);
                Ok(())
            } else {
                Err(meta.error("expected id = N or symbol = \"...\""))
            }
        })?;
    }
    if !found {
        return Err(syn::Error::new(
            Span::call_site(),
            "pod method requires #[pod_method(id = N, symbol = \"...\")]",
        ));
    }
    Ok((
        id.ok_or_else(|| syn::Error::new(Span::call_site(), "pod method is missing id"))?,
        symbol.ok_or_else(|| {
            syn::Error::new(Span::call_site(), "pod method is missing export symbol")
        })?,
    ))
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum ScalarType {
    U64,
    I32,
    MutU8Pointer,
    MutU64Pointer,
}

fn classify_signature(function: &ForeignItemFn) -> syn::Result<Signature> {
    let arguments = function
        .sig
        .inputs
        .iter()
        .map(|argument| match argument {
            syn::FnArg::Typed(argument) => scalar_type(&argument.ty),
            syn::FnArg::Receiver(receiver) => Err(syn::Error::new_spanned(
                receiver,
                "pod declarations cannot have a receiver",
            )),
        })
        .collect::<syn::Result<Vec<_>>>()?;
    let output = match &function.sig.output {
        ReturnType::Default => None,
        ReturnType::Type(_, ty) => Some(scalar_type(ty)?),
    };
    let signature = match (arguments.as_slice(), output) {
        ([], Some(ScalarType::U64)) => Signature::NoArgsU64,
        ([ScalarType::MutU8Pointer, ScalarType::U64], Some(ScalarType::I32)) => {
            // Both state+length and state+value share the machine signature.
            // The two semantic variants intentionally use the state+u64 status ID.
            Signature::StateU64Status
        }
        ([ScalarType::MutU8Pointer, ScalarType::U64, ScalarType::U64], Some(ScalarType::I32)) => {
            Signature::StateU64U64Status
        }
        (
            [
                ScalarType::MutU8Pointer,
                ScalarType::U64,
                ScalarType::MutU64Pointer,
            ],
            Some(ScalarType::I32),
        ) => Signature::StateU64OutU64Status,
        ([ScalarType::MutU8Pointer], Some(ScalarType::U64)) => Signature::StateU64,
        _ => {
            return Err(syn::Error::new_spanned(
                &function.sig,
                "unsupported pod C signature; use only the documented scalar method shapes",
            ));
        }
    };

    Ok(signature)
}

fn scalar_type(ty: &Type) -> syn::Result<ScalarType> {
    match ty {
        Type::Path(path) if simple_path(path, "u64") => Ok(ScalarType::U64),
        Type::Path(path) if simple_path(path, "i32") => Ok(ScalarType::I32),
        Type::Ptr(pointer)
            if pointer.mutability.is_some()
                && matches!(&*pointer.elem, Type::Path(path) if simple_path(path, "u8")) =>
        {
            Ok(ScalarType::MutU8Pointer)
        }
        Type::Ptr(pointer)
            if pointer.mutability.is_some()
                && matches!(&*pointer.elem, Type::Path(path) if simple_path(path, "u64")) =>
        {
            Ok(ScalarType::MutU64Pointer)
        }
        _ => Err(syn::Error::new_spanned(
            ty,
            "unsupported pod ABI type; aliases, references, aggregates, and non-scalar values are rejected",
        )),
    }
}

fn simple_path(path: &TypePath, expected: &str) -> bool {
    path.qself.is_none()
        && path.path.leading_colon.is_none()
        && path.path.segments.len() == 1
        && path.path.segments[0].ident == expected
        && path.path.segments[0].arguments.is_empty()
}
