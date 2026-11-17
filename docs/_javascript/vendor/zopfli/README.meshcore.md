<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/_javascript/vendor/zopfli/README.meshcore/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

# Zopfli WebAssembly encoder

The binary and Emscripten runtime come from `@gfx/zopfli` version 1.0.15,
published at https://github.com/gfx/universal-zopfli-js (commit
`29b826a49f4161363dc44a91031feea87808716b`). It wraps Google's Zopfli and is
distributed under the Apache 2.0 license in `LICENSE.md`.

The npm archive has SHA-512
`ee6060a62ed40fcd9fb1f7f94e140a7add2e0539780587ab42e73efea03510b4f058488879d453703dc982252ef70c19da4cad5bc24e6f5eb9ad2740b7c3df71`.
`zopfli.wasm` is the decoded, unchanged `dist/libzopfli-wasm.json` payload.
Its SHA-256 is `1b9fd611f0485bef26d0ed4e7d141a8697d5843f5860cbed1c625f5c0fd853bd`.
`libzopfli.js` is the distribution's Emscripten glue with its Node `require`
and base64 setup replaced by a locally fetched binary supplied by the worker.
The rest of the generated glue is unchanged.

MeshCore invokes raw DEFLATE with 1,000 iterations, block splitting on, and
the default 15 maximum splits. Compression runs in a worker; the target's
existing DEFLATE decoder does not need Zopfli.
