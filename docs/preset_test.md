# Temporary radio test command generator

Use this page to join the shared temporary radio test without overwriting the
node's saved primary radio settings. The default window is **Monday,
September 21, 2026 at 5:00 PM through Wednesday, September 23 at 5:00 PM
Pacific time** (48 hours), using **910.1 MHz, 500 kHz, SF8, CR7**.

The page reads the complete test definition from its URL. Change the query
parameters to reuse it for a different window or radio tuple; no source edit is
required.

<div class="preset-test" data-preset-test>
  <div class="preset-test-error" data-role="config-error" role="alert" hidden></div>

  <div data-role="content">
    <section class="preset-test-hero" aria-labelledby="preset-test-title">
      <p class="preset-test-eyebrow">MeshCore · temporary preset test</p>
      <div class="preset-test-hero-row">
        <div>
          <h2 id="preset-test-title">
            <span data-field="freq-display">910.100</span> MHz /
            <span data-field="bw-display">500</span> kHz /
            SF<span data-field="sf">8</span> /
            CR<span data-field="cr">7</span>
          </h2>
          <p data-role="window-summary"></p>
        </div>
        <span class="preset-test-status" data-role="status" data-state="before">Scheduled</span>
      </div>

      <div class="preset-test-clock" aria-live="polite">
        <span class="preset-test-clock-label" data-role="countdown-label">Starts in</span>
        <strong data-role="countdown">—</strong>
        <span data-role="countdown-detail">—</span>
      </div>

      <dl class="preset-test-window">
        <div><dt>Start</dt><dd data-role="start-pacific"></dd></div>
        <div><dt>End</dt><dd data-role="end-pacific"></dd></div>
        <div><dt>Firmware epochs</dt><dd><code data-role="epoch-range"></code></dd></div>
      </dl>
    </section>

    <section class="preset-test-warning" aria-labelledby="continuity-plan-title">
      <h2 id="continuity-plan-title">Bridges and early-revert plan</h2>
      <p>
        The test will include bridges. If the Puget Sound area experiences a
        widespread power outage or loss of cellular service, we will end the
        test early and revert to the normal 910.525 MHz channel.
      </p>
    </section>

    <section class="preset-test-warning" aria-labelledby="save-settings-title">
      <h2 id="save-settings-title">Before changing a radio</h2>
      <p>
        Run <code>get radio</code> and record the saved primary tuple. Both
        primary TempRadio methods return to that saved tuple; they do not store
        a return tuple inside the temporary command. On a Companion, also run
        <code>get radio2</code> and <code>get radio2.cross</code> so you can restore
        any non-default secondary-profile setup.
      </p>
    </section>

    <section aria-labelledby="clock-check-title">
      <h2 id="clock-check-title">Check the node clock before scheduling</h2>
      <p>
        <code>tempradioat</code> and <code>tempradioat2</code> use UTC Unix time.
        Run <code>clock</code> on every node and compare it with the browser UTC
        time below. It should agree to within about a minute. A wrong clock can
        start late, start immediately, or cause the schedule to be rejected.
      </p>

      <div class="preset-test-clock-check">
        <div>
          <span>Browser UTC now</span>
          <strong data-role="browser-utc">—</strong>
        </div>
        <div>
          <span>Browser Unix time</span>
          <strong data-role="browser-epoch">—</strong>
        </div>
      </div>

      <div class="preset-test-grid preset-test-grid--clock">
        <article class="preset-test-card">
          <h3>Remote admin session</h3>
          <p>
            In a MeshCore client that supplies the sender timestamp, sync and
            then verify:
          </p>
          <pre><code>clock sync
clock</code></pre>
        </article>

        <article class="preset-test-card">
          <h3>Local USB or browser console</h3>
          <p>Copy this fresh, run it immediately, and then verify with <code>clock</code>:</p>
          <pre><code data-command="set-clock">time 0
clock</code></pre>
          <button type="button" data-copy-command="set-clock">Copy current clock command</button>
        </article>
      </div>

      <p class="preset-test-note">
        The schedule must be queued while both its start and end are in the
        future and within the firmware's roughly 24-day scheduling horizon.
        Scheduled entries are held in RAM and disappear if the node reboots.
      </p>
    </section>

    <section aria-labelledby="join-now-title">
      <h2 id="join-now-title">Option 1: switch when the test begins</h2>
      <p>
        Open this page after the start time. Its timeout shrinks so every node
        returns at the same end time. These copy buttons remain disabled before
        the window to prevent an early switch.
      </p>

      <div class="preset-test-grid">
        <article class="preset-test-card">
          <div class="preset-test-card-heading">
            <h3>Stock repeater, room server, or sensor</h3>
            <span>Primary radio</span>
          </div>
          <pre><code data-command="stock-now"></code></pre>
          <button type="button" data-copy-command="stock-now">Copy command</button>
          <p class="preset-test-note" data-role="stock-now-note"></p>
        </article>

        <article class="preset-test-card">
          <div class="preset-test-card-heading">
            <h3>Companion using both frequencies</h3>
            <span>Dual profile</span>
          </div>
          <pre><code data-command="companion-now"></code></pre>
          <button type="button" data-copy-command="companion-now">Copy commands</button>
          <p class="preset-test-note">
            <code>rxtx</code> permits transmission on the second profile;
            <code>radio2.cross on</code> copies ordinary Companion traffic across
            the primary and temporary profiles.
          </p>
        </article>
      </div>
    </section>

    <section aria-labelledby="schedule-title">
      <h2 id="schedule-title">Option 2: schedule it in advance</h2>
      <p>
        Use this only after checking the clock above. The exact UTC epochs are
        built into the commands, so the node switches at the common start and
        restores its saved configuration at the common end.
      </p>

      <div class="preset-test-grid">
        <article class="preset-test-card">
          <div class="preset-test-card-heading">
            <h3>Stock repeater, room server, or sensor</h3>
            <span>Primary schedule</span>
          </div>
          <pre><code data-command="stock-scheduled"></code></pre>
          <button type="button" data-copy-command="stock-scheduled">Copy schedule commands</button>
          <p class="preset-test-note" data-role="stock-schedule-note"></p>
        </article>

        <article class="preset-test-card">
          <div class="preset-test-card-heading">
            <h3>Companion using both frequencies</h3>
            <span>Dual-profile schedule</span>
          </div>
          <pre><code data-command="companion-scheduled"></code></pre>
          <button type="button" data-copy-command="companion-scheduled">Copy schedule commands</button>
          <p class="preset-test-note">
            Crossing is saved independently and remains <code>on</code> after the
            temporary second profile ends. Restore its previous value after the
            test; <code>auto</code> is the normal default.
          </p>
        </article>
      </div>
    </section>

    <section aria-labelledby="cancel-title">
      <h2 id="cancel-title">Cancel or leave the test</h2>
      <p>
        Use the command for the node type and timing. The <code>all</code> forms
        remove every temporary schedule in that family. To preserve another
        schedule, first run the corresponding <code>get</code> command and replace
        <code>all</code> with its displayed entry number.
      </p>

      <div class="preset-test-cancel-grid">
        <article class="preset-test-card">
          <h3>Stock · before it starts</h3>
          <pre><code data-command="stock-cancel-before"></code></pre>
          <button type="button" data-copy-command="stock-cancel-before">Copy cancel commands</button>
        </article>

        <article class="preset-test-card">
          <h3>Stock · while it is running</h3>
          <pre><code data-command="stock-cancel-during"></code></pre>
          <button type="button" data-copy-command="stock-cancel-during">Copy restore command</button>
          <p class="preset-test-note">
            <code>normalradio</code> cancels pending and active primary TempRadio
            windows and restores the saved primary tuple after its reply drains.
          </p>
        </article>

        <article class="preset-test-card">
          <h3>Companion · before it starts</h3>
          <pre><code data-command="companion-cancel-before"></code></pre>
          <button type="button" data-copy-command="companion-cancel-before">Copy cancel commands</button>
        </article>

        <article class="preset-test-card">
          <h3>Companion · while it is running</h3>
          <pre><code data-command="companion-cancel-during"></code></pre>
          <button type="button" data-copy-command="companion-cancel-during">Copy restore commands</button>
          <p class="preset-test-note">
            If <code>get radio2.cross</code> was not <code>auto</code> before the
            test, restore that recorded value instead.
          </p>
        </article>
      </div>
    </section>

    <section aria-labelledby="share-title">
      <h2 id="share-title">Reuse this page for another test</h2>
      <p>
        Set <code>start</code> and <code>end</code> to ISO-8601 timestamps with an
        explicit UTC offset, or to Unix epoch seconds. Set <code>freq</code>,
        <code>bw</code>, <code>sf</code>, and <code>cr</code> to the desired radio
        tuple. The current configuration link is normalized to UTC.
      </p>
      <pre><code>?start=2026-09-21T17:00:00-07:00&amp;end=2026-09-23T17:00:00-07:00&amp;freq=910.1&amp;bw=500&amp;sf=8&amp;cr=7</code></pre>
      <button type="button" data-copy-command="share-url">Copy configured page URL</button>
    </section>
  </div>
</div>

The scheduled commands require current full-parser firmware. Companion
`tempradio2` and `tempradioat2` require a build with dual-radio-profile support.
If a node reports an unknown command, update it or use only a method that its
installed firmware documents.
