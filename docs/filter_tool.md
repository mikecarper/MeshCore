# Filter policy playground

Design and test policies for the proposed ground-up MeshCore forwarding engine.
The playground models phased evaluation, immutable receive-time matches,
explicit priority and stop behavior, ACL ownership, compact typed conditions,
and accumulated forwarding decisions.

Everything runs locally in this browser. Channel keys, packet facts, and policy
drafts are not uploaded anywhere.

<div class="filter-design-warning" role="note">
  <strong>Engine design preview</strong>
  <p>
    This page targets the new policy-engine idea, not today's FPF7 file or
    existing <code>set flood.*</code> commands. Its readable policy language,
    JSON, and Base64 bundle are a prototype for design testing. Current
    firmware cannot install these policies yet.
  </p>
</div>

## Build a policy

<div class="filter-tool" data-filter-tool>
  <div class="filter-tool-toolbar" aria-label="Policy examples">
    <strong>Load an example:</strong>
    <button type="button" data-example="blackhole">BlackHole86 rewrite</button>
    <button type="button" data-example="wildcards">Login and other classes</button>
    <button type="button" data-example="moderation">Content moderation</button>
    <button type="button" data-example="system">System forwarding gates</button>
    <button type="button" data-example="mixed">Full mixed policy</button>
  </div>

  <div class="filter-tool-grid">
    <section class="filter-panel filter-builder" aria-labelledby="filter-builder-title">
      <div class="filter-panel-heading">
        <div>
          <p class="filter-eyebrow">Rule builder</p>
          <h2 id="filter-builder-title">Create a policy rule</h2>
        </div>
        <button type="button" data-role="reset-form">Reset form</button>
      </div>

      <h3>Identity and execution</h3>
      <div class="filter-form-grid">
        <label>
          Stable rule ID
          <input data-field="id" value="rule-1" maxlength="32" placeholder="blackhole-rewrite">
        </label>
        <label>
          Processing phase
          <select data-field="phase">
            <option value="scope_gate">1 - Incoming scope gate</option>
            <option value="rewrite">2 - Scope rewrite</option>
            <option value="forward">3 - Forwarding decision</option>
            <option value="content">4 - Decrypted content</option>
            <option value="schedule">5 - Scheduling and retry</option>
          </select>
        </label>
        <label>
          Rule owner / capability
          <select data-field="owner">
            <option value="scope">ACL 4 - Region/scope manager</option>
            <option value="filter">ACL 5 - Filter manager</option>
            <option value="admin">Administrator</option>
            <option value="system">Firmware-managed system rule</option>
          </select>
        </label>
        <label>
          Priority
          <input data-field="priority" type="number" min="0" max="255" inputmode="numeric" value="100">
        </label>
        <label>
          Mode
          <select data-field="mode">
            <option value="active">Active</option>
            <option value="shadow">Shadow - observe only</option>
            <option value="disabled">Disabled</option>
          </select>
        </label>
        <label>
          Stop behavior after a match
          <select data-field="stop">
            <option value="none">Continue processing</option>
            <option value="phase">Stop this phase</option>
            <option value="policy">Stop later policy phases</option>
          </select>
        </label>
      </div>

      <h3>Match immutable receive-time facts</h3>
      <div class="filter-form-grid">
        <label>
          Route class
          <select data-field="route">
            <option value="flood">Any flood route</option>
            <option value="unscoped_flood">Unscoped flood only</option>
            <option value="scoped_flood">Transport-scoped flood only</option>
            <option value="direct">Direct routes</option>
            <option value="any">Any route</option>
          </select>
        </label>
        <label>
          Payload type or class
          <select data-field="type">
            <option value="any">Any payload type</option>
            <option value="class:group">Class: group text and data</option>
            <option value="class:login">Class: login/admin family</option>
            <option value="class:other">Class: every other payload</option>
            <option value="req">REQ</option>
            <option value="response">RESPONSE</option>
            <option value="txt_msg">TXT_MSG</option>
            <option value="ack">ACK</option>
            <option value="advert">ADVERT</option>
            <option value="grp_txt">GRP_TXT</option>
            <option value="grp_data">GRP_DATA</option>
            <option value="anon_req">ANON_REQ</option>
            <option value="path">PATH</option>
            <option value="trace">TRACE</option>
            <option value="multipart">MULTIPART</option>
            <option value="control">CONTROL</option>
            <option value="ota">OTA</option>
            <option value="13">Reserved type 13</option>
            <option value="14">Reserved type 14</option>
            <option value="raw_custom">RAW_CUSTOM</option>
          </select>
        </label>
        <label>
          Received hops
          <input data-field="hops" value="all" placeholder="all, 5+, 2-6, or 3">
        </label>
        <label>
          Authenticated channel (optional)
          <input data-field="channel" placeholder="#rgdata, public, or key">
        </label>
        <label>
          Original incoming scope
          <input data-field="incoming" list="policy-incoming-options" value="any" placeholder="any">
          <datalist id="policy-incoming-options">
            <option value="any"></option>
            <option value="none"></option>
            <option value="scoped"></option>
            <option value="allowed"></option>
            <option value="unknown"></option>
            <option value="scope:usa"></option>
            <option value="region:west"></option>
          </datalist>
        </label>
        <label>
          Path matcher
          <select data-field="path-kind">
            <option value="none">No path condition</option>
            <option value="prefix">Ordered 1/2/3-byte pbyte prefix</option>
            <option value="blacklist">Passive blacklist</option>
            <option value="bucket:1">Path bucket 1</option>
            <option value="bucket:2">Path bucket 2</option>
            <option value="bucket:3">Path bucket 3</option>
            <option value="bucket:4">Path bucket 4</option>
            <option value="bucket:5">Path bucket 5</option>
            <option value="bucket:6">Path bucket 6</option>
            <option value="loop:strict">Own-ID loop: strict</option>
            <option value="loop:moderate">Own-ID loop: moderate</option>
            <option value="loop:minimal">Own-ID loop: minimal</option>
          </select>
        </label>
        <label>
          Ordered pbyte prefix
          <input data-field="path-prefix" placeholder="86, 860C, or 860C,12A4">
        </label>
        <label>
          Decrypted sender (optional)
          <input data-field="sender" maxlength="31" placeholder="Noisy User">
        </label>
        <label>
          Temporary-radio state
          <select data-field="temp-radio">
            <option value="any">Either state</option>
            <option value="inactive">Inactive</option>
            <option value="active">Active</option>
          </select>
        </label>
        <label>
          Minimum SNR dB (optional)
          <input data-field="snr-min" type="number" min="-30" max="30" step="0.25" placeholder="no minimum">
        </label>
        <label>
          Maximum SNR dB (optional)
          <input data-field="snr-max" type="number" min="-30" max="30" step="0.25" placeholder="no maximum">
        </label>
      </div>

      <h3>Accumulate forwarding actions</h3>
      <div class="filter-form-grid">
        <label>
          Forwarding verdict
          <select data-field="verdict">
            <option value="continue">No verdict change</option>
            <option value="drop">Drop / do not retransmit</option>
          </select>
        </label>
        <label>
          Region-gate decision
          <select data-field="scope-gate">
            <option value="unchanged">Leave global gate unchanged</option>
            <option value="require_allowed">Require original allowed region</option>
            <option value="bypass_global">Bypass global gate for this packet</option>
          </select>
        </label>
        <label>
          Scope rewrite target
          <select data-field="target-kind">
            <option value="none">Do not rewrite scope</option>
            <option value="scope">Regionless public scope</option>
            <option value="region">Configured region</option>
          </select>
        </label>
        <label>
          Target name
          <input data-field="target" placeholder="BlackHole86">
        </label>
        <label>
          Token rate per minute (optional)
          <input data-field="rate" type="number" min="1" max="65534" inputmode="numeric" placeholder="unlimited">
        </label>
        <label>
          Burst tokens
          <input data-field="burst" type="number" min="1" max="65534" inputmode="numeric" placeholder="defaults to rate">
        </label>
        <label>
          Rewrite timing
          <select data-field="timing">
            <option value="inherit">Inherit normal behavior</option>
            <option value="fast">Fast-track rewrite</option>
            <option value="normal">Normal queue timing</option>
            <option value="slow">Slow receive/transmit timing</option>
          </select>
        </label>
        <label>
          Queue priority
          <select data-field="queue">
            <option value="inherit">Inherit</option>
            <option value="high">High</option>
            <option value="normal">Normal</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label>
          Retry path bucket
          <select data-field="retry-bucket">
            <option value="none">No retry action</option>
            <option value="bucket:1">Bucket 1</option>
            <option value="bucket:2">Bucket 2</option>
            <option value="bucket:3">Bucket 3</option>
            <option value="bucket:4">Bucket 4</option>
            <option value="bucket:5">Bucket 5</option>
            <option value="bucket:6">Bucket 6</option>
          </select>
        </label>
        <label>
          Retry attempts
          <input data-field="retry-attempts" type="number" min="1" max="10" inputmode="numeric" placeholder="1">
        </label>
        <label>
          Decision tag (optional)
          <input data-field="tag" maxlength="24" placeholder="blackhole-ingress">
        </label>
      </div>

      <div class="filter-live-preview">
        <div>
          <span class="filter-preview-label">Readable policy definition</span>
          <code data-role="live-command"></code>
        </div>
        <p data-role="live-explanation"></p>
        <ul class="filter-inline-warnings" data-role="live-warnings" hidden></ul>
      </div>

      <div class="filter-builder-actions">
        <button class="filter-primary-action" type="button" data-role="save-rule">Add rule to policy</button>
        <button type="button" data-role="copy-live">Copy definition</button>
      </div>
    </section>

    <section class="filter-panel filter-policy" aria-labelledby="filter-policy-title">
      <div class="filter-panel-heading">
        <div>
          <p class="filter-eyebrow">Policy draft</p>
          <h2 id="filter-policy-title">Rules in execution order</h2>
        </div>
        <button type="button" data-role="clear-policy">Clear policy</button>
      </div>
      <div class="filter-policy-target">
        <label>
          Approximate target budget
          <select data-role="target-profile">
            <option value="stm32">Compact STM32 - 2 KB</option>
            <option value="nrf52" selected>nRF52 - 8 KB</option>
            <option value="esp32">Classic ESP32 - 16 KB</option>
            <option value="esp32_roomy">Roomy ESP32 - 64 KB</option>
          </select>
        </label>
      </div>
      <p class="filter-field-help">
        Rules match the same immutable packet facts. Ordering is phase, then
        descending priority, then stable rule ID. Drop decisions are sticky.
      </p>
      <div class="filter-empty-state" data-role="empty-policy">
        Add a rule or load an example to start exploring.
      </div>
      <ol class="filter-rule-list" data-role="rule-list"></ol>
      <div class="filter-policy-summary" data-role="policy-summary"></div>
      <ul class="filter-inline-warnings" data-role="policy-warnings" hidden></ul>
    </section>
  </div>

  <section class="filter-panel filter-simulator" aria-labelledby="filter-simulator-title">
    <div class="filter-panel-heading">
      <div>
        <p class="filter-eyebrow">Immutable packet simulator</p>
        <h2 id="filter-simulator-title">Explain an evaluation</h2>
      </div>
      <button type="button" data-role="reset-packet">Reset packet</button>
    </div>
    <div class="filter-form-grid filter-simulator-facts">
      <label>
        Received route
        <select data-packet="route">
          <option value="unscoped_flood">Unscoped flood</option>
          <option value="scoped_flood">Transport-scoped flood</option>
          <option value="direct">Direct route</option>
        </select>
      </label>
      <label>
        Payload type
        <select data-packet="type">
          <option value="req">REQ</option>
          <option value="response">RESPONSE</option>
          <option value="txt_msg">TXT_MSG</option>
          <option value="ack">ACK</option>
          <option value="advert">ADVERT</option>
          <option value="grp_txt">GRP_TXT</option>
          <option value="grp_data" selected>GRP_DATA</option>
          <option value="anon_req">ANON_REQ</option>
          <option value="path">PATH</option>
          <option value="trace">TRACE</option>
          <option value="multipart">MULTIPART</option>
          <option value="control">CONTROL</option>
          <option value="ota">OTA</option>
          <option value="13">Reserved type 13</option>
          <option value="14">Reserved type 14</option>
          <option value="raw_custom">RAW_CUSTOM</option>
        </select>
      </label>
      <label>
        Received hops
        <input data-packet="hops" type="number" min="0" max="63" inputmode="numeric" value="4">
      </label>
      <label>
        Authenticated channel
        <input data-packet="channel" value="#rgdata" placeholder="blank if none">
      </label>
      <label>
        Received pbyte path
        <input data-packet="path" value="860C,12A4" placeholder="same-width IDs">
      </label>
      <label>
        Original scope status
        <select data-packet="scope-status">
          <option value="none">Unscoped</option>
          <option value="allowed">Allowed region</option>
          <option value="unknown">Unknown or denied scope</option>
        </select>
      </label>
      <label>
        Original scope name
        <input data-packet="scope-name" placeholder="usa">
      </label>
      <label>
        Resolved region name
        <input data-packet="region-name" placeholder="west">
      </label>
      <label>
        Decrypted sender
        <input data-packet="sender" placeholder="Noisy User">
      </label>
      <label>
        Received SNR dB
        <input data-packet="snr" type="number" min="-30" max="30" step="0.25" value="6">
      </label>
      <label>
        Passive blacklist result
        <select data-packet="blacklist">
          <option value="no">No match</option>
          <option value="yes">Matched</option>
        </select>
      </label>
      <label>
        Matching path buckets
        <input data-packet="buckets" placeholder="1,3,6">
      </label>
      <label>
        Own-ID loop result
        <select data-packet="loop-level">
          <option value="0">No loop match</option>
          <option value="1">Strict only</option>
          <option value="2">Moderate and strict</option>
          <option value="3">Minimal, moderate, and strict</option>
        </select>
      </label>
      <label class="filter-simulator-check">
        <input data-packet="temp-radio" type="checkbox">
        Temporary radio is active
      </label>
    </div>
    <div class="filter-builder-actions">
      <button class="filter-primary-action" type="button" data-role="simulate">Run policy simulation</button>
    </div>
    <div class="filter-error" data-role="simulation-error" role="alert" hidden></div>
    <div class="filter-decision" data-role="simulation-result" hidden></div>
  </section>

  <section class="filter-panel filter-import" aria-labelledby="filter-import-title">
    <div class="filter-panel-heading">
      <div>
        <p class="filter-eyebrow">Import and explain</p>
        <h2 id="filter-import-title">Paste readable policy or a saved draft</h2>
      </div>
    </div>
    <p class="filter-field-help">
      Accepts one-line <code>policy set ... when ... do ...</code> definitions,
      playground JSON, or a playground Base64 bundle.
    </p>
    <textarea data-role="import-input" spellcheck="false" placeholder="policy set blackhole phase=rewrite owner=scope priority=150 when route=flood type=grp_data hops=4+ channel=#rgdata rx.scope=none do scope=#BlackHole86 timing=fast stop=phase"></textarea>
    <div class="filter-builder-actions">
      <button class="filter-primary-action" type="button" data-role="explain-input">Explain input</button>
      <button type="button" data-role="load-input">Load into builder</button>
    </div>
    <div class="filter-error" data-role="import-error" role="alert" hidden></div>
    <div class="filter-explain-results" data-role="explain-results" hidden></div>
  </section>

  <section class="filter-panel filter-export" aria-labelledby="filter-export-title">
    <div class="filter-panel-heading">
      <div>
        <p class="filter-eyebrow">Export</p>
        <h2 id="filter-export-title">Move or save this design</h2>
      </div>
    </div>
    <div class="filter-export-tabs" role="tablist" aria-label="Policy export format">
      <button type="button" data-export-tab="dsl" aria-selected="true">Readable policy</button>
      <button type="button" data-export-tab="json" aria-selected="false">Policy JSON</button>
      <button type="button" data-export-tab="bundle" aria-selected="false">Playground Base64</button>
    </div>
    <div data-export-panel="dsl">
      <p class="filter-field-help">Proposed human-readable definition. Current firmware does not accept it yet.</p>
      <textarea data-role="export-dsl" readonly spellcheck="false"></textarea>
    </div>
    <div data-export-panel="json" hidden>
      <p class="filter-field-help">Structured draft used by this page.</p>
      <textarea data-role="export-json" readonly spellcheck="false"></textarea>
    </div>
    <div data-export-panel="bundle" hidden>
      <p class="filter-field-help">Browser-playground interchange only. It is not the final packed firmware codec.</p>
      <textarea data-role="export-bundle" readonly spellcheck="false"></textarea>
    </div>
    <div class="filter-builder-actions">
      <button type="button" data-role="copy-export">Copy visible export</button>
      <button type="button" data-role="download-json">Download JSON</button>
    </div>
  </section>
</div>

## Proposed evaluation contract

The simulator uses these rules:

1. Every matcher reads the same immutable receive-time packet facts.
2. Rules run by phase, descending priority, then stable rule ID.
3. A drop decision is sticky and cannot be undone by a later rule.
4. The first matching scope, timing, queue, and retry action in execution order
   wins.
5. All matching token-bucket rate constraints remain attached to the decision.
6. `stop=phase` skips later rules in that phase. `stop=policy` skips later
   configurable rules, but never mandatory packet validation or radio safety.
7. Shadow rules report what they would do without changing the decision or
   stopping other rules.
8. Expensive facts such as channel authentication, decryption, and path-table
   lookup are resolved once per packet and reused by every matching rule.

The byte-budget display is deliberately approximate until the packed firmware
codec exists. It demonstrates why simple mappings should not reserve a maximum-
sized structure for every possible condition and action.
