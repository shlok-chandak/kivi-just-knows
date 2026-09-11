/// <reference types="vite/client" />

// Declares the ambient types Vite provides, including side-effect CSS
// imports. Without it an editor flags every `import "./thing.css"` as an
// unresolved module, which buries real type errors in noise.
