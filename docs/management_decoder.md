# Management report decoder

Paste MGR1 management-report payloads or complete GroupData packets from a
packet analyzer to read a radio's public management information. Everything,
including password authentication and ACL decryption, happens locally in this
browser. The report, password, and candidate public key are never uploaded.

The public portion of a report is deliberately plaintext, but it is **not
authenticated** until a management password is supplied. A password-authenticated
page proves that its public fields and encrypted ACL bytes have not been altered
by someone who does not know that password.

## Decode a management report

<div class="management-tool" data-management-decoder>
  <label for="management-packet-input">MGR1 payload or complete GroupData packet hex</label>
  <textarea
    id="management-packet-input"
    data-role="input"
    spellcheck="false"
    autocomplete="off"
    placeholder="Paste analyzer Raw Data, canonical MGR1 payload hex, or one MQTT raw value per line"
    aria-describedby="management-packet-help"
  ></textarea>
  <p class="management-help" id="management-packet-help">
    Spaces, line breaks, colons, dashes, <code>0x</code>, and MQTT JSON fields named
    <code>raw</code> or <code>data</code> are accepted. Paste all pages from one report
    together to view a complete multi-page ACL. Press Ctrl/Command+Enter to decode.
  </p>

  <label for="management-password-input">Management password <span>(optional for public fields; required for ACLs)</span></label>
  <input id="management-password-input" data-role="password" type="password" autocomplete="new-password">
  <p class="management-help">
    The password remains in this page only. The decoder derives the MGR1 AES-SIV key in
    your browser and does not send it anywhere.
  </p>

  <label for="management-candidate-input">Candidate administrator public key <span>(optional)</span></label>
  <input id="management-candidate-input" data-role="candidate" type="text" autocomplete="off" spellcheck="false" placeholder="64 hexadecimal characters">
  <p class="management-help">
    ACL encryption reveals per-radio 12-byte fingerprints, not recoverable public keys.
    Supplying a complete candidate key checks whether its fingerprint appears in this report.
  </p>

  <div class="management-actions">
    <button class="management-primary-action" type="button" data-role="decode">Decode management report</button>
    <button type="button" data-role="clear">Clear local data</button>
    <button type="button" data-role="example">Load authenticated example</button>
  </div>

  <div class="management-error" data-role="error" role="alert" aria-live="polite" hidden></div>

  <section class="management-results" data-role="results" aria-live="polite" hidden>
    <div class="management-status" data-role="status"></div>
    <dl class="management-summary" data-role="summary"></dl>
    <div class="management-warnings" data-role="warnings" hidden>
      <strong>Decode notes</strong>
      <ul data-role="warning-list"></ul>
    </div>
    <h2>Encrypted ACL entries</h2>
    <div class="management-table-wrap">
      <table class="management-table" data-role="acl-table"></table>
    </div>
  </section>
</div>

## What the decoder accepts

- Complete `PAYLOAD_TYPE_GRP_DATA` (`0x06`) analyzer/MQTT packet hex. It checks
  the MeshCore route header, encoded path length, MGR1 page bounds, and required
  zero padding.
- A canonical MGR1 payload beginning with `4D475231` (`MGR1`).
- One raw packet or canonical payload per line; duplicate observations of an
  identical page are deduplicated.

Use the same password configured by `set mgmt.password`. A decoded ACL lists
the report-specific fingerprints and the administrator and/or OTA-signer flags.
It cannot turn a fingerprint back into a full key. Use the optional candidate
field to test a specific full public key.

For the report schedule, public-field layout, cryptographic design, and the
offline Python capture tool, see [Management reports](management_reports.md).
