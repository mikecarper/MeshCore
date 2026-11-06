<!-- meshcore-hosted-doc-link:start -->
<p class="meshcore-hosted-doc-link"><a href="https://mikecarper.github.io/MeshCore/_javascript/vendor/detools-js/NOTICE/">View this page on MeshCore Docs</a>.</p>
<!-- meshcore-hosted-doc-link:end -->

This is a browser adaptation of `detools-js` 0.1.0 by Ahmet Ucar, from
`https://registry.npmjs.org/detools-js/-/detools-js-0.1.0.tgz` (SHA-256
`2553d4826ccbc64ff5b99032cc81ec8f995452c86866defc82b234a94987fedb`).
The package's MIT license is in `LICENSE`. Only `bsdiff.js`, `pack-size.js`
and `compression/crle.js` are included; Node `Buffer` allocations were changed
to `Uint8Array`, and each file was wrapped for browsers and CommonJS.

The bsdiff algorithm derives from Colin Percival's implementation, distributed
under the BSD 2-Clause license:

Copyright 2003-2005 Colin Percival. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
