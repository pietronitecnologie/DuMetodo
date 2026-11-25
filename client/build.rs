//! Icona del file .exe su Windows (solo quando si compila SU Windows: serve il compilatore di risorse).
fn main() {
    println!("cargo:rerun-if-changed=risorse/metodo.ico");
    #[cfg(windows)]
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut risorsa = winresource::WindowsResource::new();
        risorsa.set_icon("risorse/metodo.ico");
        risorsa.set("ProductName", "Metodo v2 Client");
        risorsa.set("FileDescription", "Metodo v2 Client");
        if let Err(e) = risorsa.compile() {
            println!("cargo:warning=icona dell'eseguibile non applicata: {e}");
        }
    }
}
