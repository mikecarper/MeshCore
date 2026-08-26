# Filter policy playground

Build a forwarding policy, see its readable definition, and simulate how a
repeater handles a packet. Rules match received packet facts, then apply actions
such as dropping, scoping, rate-limiting, or retrying a flood.

Everything runs locally in this browser. Channel keys, packet facts, and policy
drafts are not uploaded anywhere.

<div class="filter-design-warning" role="note">
  <strong>Policy design preview</strong>
  <p>
    The phases and core conditions model current FPF7 behavior, including its
    forward rows, scope rewrites, and shared blacklist. The readable policy
    language, JSON, and Base64 bundle are still a prototype: current firmware
    is configured with <code>set flood.*</code> commands and cannot install a
    bundle from this page.
  </p>
</div>

## Build a policy

Start with an example or build a rule, then test the draft against packet facts
in the simulator below. The examples draw from
[Flood Filtering and Moderation](flood_filtering.md).

<div class="filter-tool" data-filter-tool>
  <div class="filter-management-safety" role="note">
    <strong>Remote login and direct routes</strong>
    <p>
      This policy only decides whether a relay retransmits a flood. Direct
      packets and local delivery are unaffected. Rules matching the login/admin
      family can still reduce multi-hop remote-login reach, so the analyzer
      flags them.
    </p>
  </div>
  <div class="filter-tool-toolbar" aria-label="Policy examples">
    <div class="filter-example-heading">
      <strong>Example policies</strong>
      <span>Choose one to load its full rules into the builder and draft.</span>
    </div>
    <div class="filter-example-primer" role="note">
      <p>
        Read each summary as <code>when</code> all conditions match,
        <code>do</code> the actions. A match alone does not stop forwarding.
      </p>
      <div class="filter-primer-tables">
        <table class="filter-primer-table">
          <caption>Payload selectors</caption>
          <thead>
            <tr><th><code>type=</code> value</th><th>Matches</th></tr>
          </thead>
          <tbody>
            <tr><td><a href="#payload-type-reference"><code>grp_data</code></a></td><td>Only the named payload type; see every exact type below</td></tr>
            <tr><td><code>any</code></td><td>Every payload type</td></tr>
            <tr><td><code>class:group</code></td><td>GRP_TXT and GRP_DATA</td></tr>
            <tr><td><code>class:login</code></td><td>REQ, RESPONSE, TXT_MSG, ANON_REQ, and PATH</td></tr>
            <tr><td><code>class:other</code></td><td>Every remaining payload type that is not group or login</td></tr>
          </tbody>
        </table>
        <table class="filter-primer-table">
          <caption>Other conditions and actions</caption>
          <thead>
            <tr><th>Field</th><th>Meaning</th></tr>
          </thead>
          <tbody>
            <tr><td><code>hops=</code></td><td>Received hop count: <code>all</code>, <code>3+</code>, <code>2-6</code>, or <code>3</code></td></tr>
            <tr><td><code>channel=</code></td><td><code>*</code> means no channel condition; a name or key authenticates one group channel; <code>hash:XX</code> is an unauthenticated one-byte fallback</td></tr>
            <tr><td><code>rx.scope=</code></td><td>Original incoming transport scope</td></tr>
            <tr><td><code>path=</code></td><td>Path prefix, blacklist, bucket, or loop match</td></tr>
            <tr><td><code>tempradio=</code></td><td>Temporary-radio state</td></tr>
            <tr><td><code>do drop</code></td><td>Do not retransmit</td></tr>
            <tr><td><code>do scope=</code></td><td>Set the outgoing transport scope</td></tr>
            <tr><td><code>do rate=</code></td><td>Apply a per-minute rate and burst</td></tr>
            <tr><td><code>do timing=</code></td><td>Select fast, normal, or slow scheduling</td></tr>
          </tbody>
        </table>
      </div>
      <p class="filter-primer-note">
        There is no <code>class:txt</code>. Use <code>type=grp_txt</code> for
        channel text or <code>type=txt_msg</code> for peer text.
      </p>
    </div>
    <div class="filter-example-grid">
      <button type="button" data-example="channel_scope">
        <span>Set #BlackHole86 scope on all #rgdata group traffic</span>
        <code>when type=class:group hops=all channel=#rgdata do scope=#BlackHole86 timing=fast</code>
      </button>
      <button type="button" data-example="blackhole">
        <span>Add #BlackHole86 scope to unscoped #rgdata data</span>
        <code>when type=grp_data hops=all channel=#rgdata rx.scope=none do scope=#BlackHole86 timing=fast</code>
      </button>
      <button type="button" data-example="scope_rewrite">
        <span>Replace #usa with #BlackHole86 on #rgdata data</span>
        <code>when type=grp_data hops=all channel=#rgdata rx.scope=scope:usa do scope=#BlackHole86 timing=fast</code>
      </button>
      <button type="button" data-example="prefix_rate">
        <span>Rate-limit packets whose path starts with 860C</span>
        <code>when type=any hops=all path=prefix:860C do rate=10/min burst=10</code>
      </button>
      <button type="button" data-example="high_traffic">
        <span>Drop selected flood types at their hop limits</span>
        <code>when type=control hops=1+ do drop<br>when type=req hops=3+ do drop; same for type=grp_data<br>when type=response hops=9+ do drop; same for type=anon_req and type=path</code>
      </button>
      <button type="button" data-example="moderation">
        <span>Rate-limit Public messages from “Noisy User”</span>
        <code>when type=grp_txt hops=all channel=public sender="Noisy User" do rate=5/min burst=5</code>
      </button>
      <button type="button" data-example="blacklist">
        <span>Stop forwarding traffic from blacklisted internet gateways</span>
        <code>when type=any hops=all path=blacklist do drop</code>
      </button>
      <button type="button" data-example="factory">
        <span>Drop OTA outside temporary-radio mode and distant #wardriving</span>
        <code>when type=ota hops=all tempradio=inactive do drop<br>when type=any channel=#wardriving hops=5+ do drop</code>
      </button>
      <button type="button" data-example="wildcards">
        <span>Set #BlackHole86 scope on login and bucket-matched other traffic</span>
        <code>when type=class:login hops=all do scope=#BlackHole86 timing=fast<br>when type=class:other hops=all path=bucket:2 do scope=#BlackHole86 timing=slow</code>
      </button>
    </div>
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

      <h3>Common match settings</h3>
      <div class="filter-form-grid">
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
          Channel (optional)
          <input data-field="channel" placeholder="*, #rgdata, public, hash:A7, or key">
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
      </div>

      <h3>Common actions</h3>
      <p class="filter-field-help">
        Choosing a phase-specific action automatically selects a compatible
        phase and ACL owner. Open the advanced section to inspect or override
        those choices.
      </p>
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

      <details class="filter-advanced">
        <summary>Advanced execution, ownership, and flood route</summary>
        <p class="filter-field-help">
          Direct packets already carry a supplied path and are intentionally
          outside this engine. The route condition below only distinguishes
          unscoped floods from transport-scoped floods.
        </p>
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
            Rule processing after a match
            <select data-field="stop">
              <option value="none">Continue to later rules</option>
              <option value="phase">Skip later rules in this phase</option>
              <option value="policy">Skip all later policy rules</option>
            </select>
          </label>
          <label>
            Flood route
            <select data-field="route">
              <option value="flood">Either flood route</option>
              <option value="unscoped_flood">Unscoped flood only</option>
              <option value="scoped_flood">Transport-scoped flood only</option>
            </select>
          </label>
        </div>
      </details>

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
    <div class="filter-packet-reader">
      <div class="filter-packet-reader-heading">
        <div>
          <h3>Read a raw MeshCore packet</h3>
          <p class="filter-field-help">
            Paste an on-air packet as hexadecimal. The decoder follows the
            <a href="../packet_format/">MeshCore wire format</a>, displays its
            header, route, pbyte path, and clear payload envelope, then loads
            only facts actually present on the wire into the simulator.
          </p>
        </div>
        <span>Runs locally</span>
      </div>
      <label>
        Raw packet hex
        <textarea data-role="raw-packet-input" rows="3" spellcheck="false">014e912ceebb98918b86772df5dacf1bcba9e4127ffffaa8665596aa3e2903a3b1901fdc53497dfca6b5d7df2d771bea68de</textarea>
      </label>
      <div class="filter-builder-actions">
        <button class="filter-primary-action" type="button" data-role="decode-packet">Decode and load packet</button>
      </div>
      <div class="filter-error" data-role="packet-decode-error" role="alert" hidden></div>
      <div class="filter-packet-decode" data-role="packet-decode-result" aria-live="polite" hidden></div>
    </div>
    <div class="filter-form-grid filter-simulator-facts">
      <label>
        Received route
        <select data-packet="route">
          <option value="unscoped_flood">Unscoped flood</option>
          <option value="scoped_flood">Transport-scoped flood</option>
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
        Channel
        <input data-packet="channel" value="#rgdata" placeholder="#name, public, hash:A7, key, or blank">
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
    <textarea data-role="import-input" spellcheck="false" placeholder="policy set rgdata-scope phase=rewrite owner=scope priority=150 when route=flood type=grp_data hops=all channel=#rgdata rx.scope=none do scope=#BlackHole86 timing=fast"></textarea>
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

## Payload type reference

Use an exact `type=` value when a rule should match only one payload type.
The class column shows which broader selector also matches it.

| `type=` value | Payload | Class |
| --- | --- | --- |
| `req` | Request | `class:login` |
| `response` | Response | `class:login` |
| `txt_msg` | Peer text message | `class:login` |
| `ack` | Acknowledgment | `class:other` |
| `advert` | Node advertisement | `class:other` |
| `grp_txt` | Group-channel text | `class:group` |
| `grp_data` | Group-channel datagram | `class:group` |
| `anon_req` | Anonymous request | `class:login` |
| `path` | Returned path | `class:login` |
| `trace` | Path trace | `class:other` |
| `multipart` | One frame in a multipart sequence | `class:other` |
| `control` | Control or discovery data | `class:other` |
| `ota` | OTA-over-LoRa data | `class:other` |
| `13` | Reserved payload type 13 | `class:other` |
| `14` | Reserved payload type 14 | `class:other` |
| `raw_custom` | Application-defined raw data | `class:other` |

## Current FPF7 command mapping

| Firmware command | FPF7 role |
| --- | --- |
| `flood.rule` / `flood.filter` | Forward-phase match and action rows |
| `flood.channel.data` | Compatibility view over one visible `type=grp_data` forward drop row |
| `flood.channel.scope` | Scope-rewrite phase rows |
| `flood.filter.blacklist` | One shared unordered path-ID set referenced by `path=blacklist` rows |

Generalized repeaters expose 63 forward rows and commit those sections
together. The blacklist is useful
for refusing to retransmit floods associated with internet gateways dumping
bulk traffic, but a path ID is truncated and unauthenticated; it identifies a
routing pattern, not a person.

## Proposed evaluation contract

The simulator uses these rules:

1. Only flood retransmission enters the policy. Direct routing and local packet
   delivery remain outside it.
2. Every matcher reads the same immutable receive-time packet facts.
3. Rules run by phase, descending priority, then stable rule ID.
4. A drop decision is sticky and cannot be undone by a later rule.
5. The first matching scope, timing, queue, and retry action in execution order
   wins.
6. All matching token-bucket rate constraints remain attached to the decision.
7. `stop=phase` skips later rules in that phase. `stop=policy` skips later
   configurable rules, but never mandatory packet validation or radio safety.
8. Shadow rules report what they would do without changing the decision or
   stopping other rules.
9. Expensive facts such as channel-key matching, decryption, and path-table
   lookup are resolved once per packet and reused by every matching rule.

The byte-budget display is deliberately approximate until the packed firmware
codec exists. It demonstrates why simple mappings should not reserve a maximum-
sized structure for every possible condition and action.
