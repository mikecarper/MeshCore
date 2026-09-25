# <span data-role="preset-test-page-title">Temporary radio test builder</span>

<span data-role="preset-test-page-summary">Choose a window and radio tuple to
create a shareable temporary-radio test URL.</span>

<div class="preset-test" data-preset-test>
  <div class="preset-test-error" data-role="config-error" role="alert" hidden></div>

  <div data-role="content">
    <div data-role="test-content" hidden>
    <section class="preset-test-hero" aria-labelledby="preset-test-title">
      <p class="preset-test-eyebrow" data-role="preset-test-eyebrow">MeshCore · default 48-hour temporary preset test</p>
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

      <div class="preset-test-quick-command" aria-labelledby="stock-test-title">
        <h3 id="stock-test-title">Stock firmware: copy this <code>tempradio</code> command</h3>
        <pre><code data-command="stock-now"></code></pre>
        <button type="button" data-copy-command="stock-now">Copy tempradio command</button>
        <p class="preset-test-note" data-role="stock-now-note"></p>
        <p class="preset-test-note">
          Before sending it, run <code>get radio</code> and save the result.
          No clock setup is needed. Your saved settings return automatically.
        </p>
      </div>

      <div class="preset-test-clock" aria-live="polite">
        <span class="preset-test-clock-label" data-role="countdown-label">Starts in</span>
        <strong data-role="countdown">—</strong>
        <span data-role="countdown-detail">—</span>
      </div>

      <dl class="preset-test-window">
        <div><dt>Start</dt><dd data-role="start-zoned"></dd></div>
        <div><dt>End</dt><dd data-role="end-zoned"></dd></div>
        <div><dt>Display time zone</dt><dd data-role="display-zone"></dd></div>
      </dl>
    </section>

    <details class="preset-test-generator-disclosure preset-test-stock-leave">
      <summary><h2>Need to leave the test early?</h2></summary>
      <div class="preset-test-generator-disclosure-body">
        <p>Send this command to return to your saved radio settings in one minute:</p>
        <pre><code data-command="stock-cancel-during"></code></pre>
        <button type="button" data-copy-command="stock-cancel-during">Copy leave command</button>
      </div>
    </details>

    </div>

    <div data-role="test-content-footer" hidden>
    <details class="preset-test-generator-disclosure preset-test-keymind-disclosure"
             data-role="keymind-disclosure">
      <summary><h2>KeyMind Cascade firmware and advanced commands</h2></summary>
      <div class="preset-test-generator-disclosure-body">
      <p>
        Use these only if your firmware supports the listed commands or you need
        dual-radio operation, scheduling, or clock correction.
      </p>

      <section class="preset-test-schedule-mode" aria-labelledby="schedule-mode-title">
        <h2 id="schedule-mode-title">Scheduled command timing</h2>
        <fieldset>
          <legend>Choose the syntax supported by the node</legend>
          <label>
            <input type="radio" name="schedule-command-mode" value="relative" checked>
            <span><strong>KeyMind Cascade · <code>+minutes</code></strong> (default)</span>
          </label>
          <label>
            <input type="radio" name="schedule-command-mode" value="absolute">
            <span><strong>Absolute Unix time</strong></span>
          </label>
        </fieldset>
        <p data-role="relative-schedule-note">
          Relative commands calculate whole minutes from now to the shared start
          and end. Both offsets use the node's same command-time snapshot, so no
          clock correction is needed. Copy the command shortly after it is shown.
        </p>
        <p data-role="absolute-schedule-note" hidden>
          Absolute commands use fixed UTC epochs. Verify or compensate for the
          node clock with the controls below before queuing them.
        </p>
      </section>

    <section class="preset-test-warning" aria-labelledby="continuity-plan-title">
      <h2 id="continuity-plan-title">Bridges and early-revert plan</h2>
      <p>
        The test will include bridges. If the Puget Sound area experiences a
        widespread power outage or loss of cellular service, we will end the
        test early. A stock node's one-minute temporary test profile then ends
        on that node's own saved radio settings.
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

    <div data-role="absolute-clock-controls" hidden>
      <p>Firmware schedule epochs: <code data-role="epoch-range"></code></p>
    <section aria-labelledby="clock-check-title">
      <h2 id="clock-check-title">Check the node clock before scheduling</h2>
      <p>
        The primary <code>tempradioat</code> and Companion
        <code>tempradioat2</code> schedulers use UTC Unix time. Run
        <code>clock</code> on every node that will be scheduled and compare it
        with the browser UTC time below. It should agree to within about a minute.
        A wrong clock can start late, start immediately, or cause the schedule to
        be rejected.
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

      <div class="preset-test-clock-override">
        <label>
          <span>Node clock reported by <code>clock</code> (UTC, optional)</span>
          <input
            type="text"
            inputmode="text"
            autocomplete="off"
            spellcheck="false"
            data-role="node-clock-input"
            placeholder="02:42 22/9/2026 UTC"
          >
          <small>
            Paste the node's <code>clock</code> reply immediately after it arrives.
            The firmware form <code>02:42 - 22/9/2026 UTC</code> is also accepted.
            Leave this blank to keep the normal UTC schedule epochs unchanged.
          </small>
        </label>
        <button type="button" data-action="apply-node-clock">Apply clock conversion</button>
      </div>
      <p class="preset-test-clock-status" data-role="node-clock-status" aria-live="polite">
        No node-clock correction is applied. Scheduled commands use the normal UTC epochs.
      </p>

      <p class="preset-test-note">
        This changes only the absolute <code>tempradioat</code> and
        <code>tempradioat2</code> epochs shown on this page; it does not issue a
        clock-setting command. The correction is for this node and this open page
        only: it is never added to generated URLs, and is cleared on reload. If
        the node clock later syncs or jumps, delete and queue the schedule again.
      </p>

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

        <article class="preset-test-card preset-test-card--wide">
          <h3>If the clock is ahead and cannot move backward</h3>
          <p>
            If setting the correct time returns <code>ERR: clock cannot go backwards</code>,
            run the command below. It resets the clock to an older fallback date and
            reboots the node immediately, so it sends no reply. After the node reconnects,
            copy and run the fresh local clock command above, verify with <code>clock</code>,
            and then add the Companion <code>tempradioat2</code> schedule again. The
            reboot clears any pending scheduled entries.
          </p>
          <p>
            If resetting or correcting this remote node's clock is not safe, leave
            it unchanged and use the optional node-clock conversion above instead.
          </p>
          <pre><code data-command="reset-clock">clkreboot</code></pre>
          <button type="button" data-copy-command="reset-clock">Copy clock reset command</button>
        </article>
      </div>

      <p class="preset-test-note">
        The Companion schedule must be queued while both its start and end are
        in the future and within the firmware's roughly 24-day scheduling
        horizon. Scheduled entries are held in RAM and disappear if the node
        reboots.
      </p>
    </section>
    </div>

    <section aria-labelledby="join-now-title">
      <h2 id="join-now-title">Companion: use both frequencies</h2>
      <p>
        These commands become available one hour before the official start.
        Their timeout shrinks so the Companion returns at the same end time.
      </p>

      <div class="preset-test-grid">
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
      <h2 id="schedule-title">Schedule a supported node in advance</h2>
      <p>
        A Simple Repeater build can schedule its primary radio with
        <code>tempradioat</code>. A Companion can instead schedule its second
        profile with <code>tempradioat2</code>. The default KeyMind Cascade form
        uses <code>+minutes</code> for both endpoints. Switch to Absolute Unix time
        above for fixed UTC epochs and the node-clock correction tools. Both forms
        switch at the common start and restore saved settings at the common end.
        Room-server and sensor roles use the stock join command above instead.
      </p>

      <div class="preset-test-grid">
        <article class="preset-test-card">
          <div class="preset-test-card-heading">
            <h3>Room server or sensor</h3>
            <span>No advance schedule</span>
          </div>
          <p class="preset-test-note">
            Use the stock <code>tempradio</code> command above after
            its setup window opens.
          </p>
        </article>

        <article class="preset-test-card">
          <div class="preset-test-card-heading">
            <h3>Simple Repeater primary radio</h3>
            <span>Primary schedule</span>
          </div>
          <pre><code data-command="primary-scheduled"></code></pre>
          <button type="button" data-copy-command="primary-scheduled">Copy schedule commands</button>
          <p class="preset-test-note">
            This is available only on a Simple Repeater build with primary
            scheduled-radio support. It changes the primary tuple at the start
            and restores the saved tuple at the end. If <code>get tempradioat</code>
            returns <code>Error: unsupported</code>, use the stock join command
            above instead.
          </p>
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
      <h2 id="cancel-title">Cancel a schedule or adjust the timeout</h2>
      <p>
        For a primary or Companion schedule, the <code>all</code> form removes every temporary
        schedule in that family. To preserve another schedule, first run the
        corresponding <code>get</code> command and replace <code>all</code> with
        its displayed entry number.
      </p>

      <div class="preset-test-cancel-grid">
        <article class="preset-test-card">
          <h3>Stock: leave in 30 minutes</h3>
          <pre><code data-command="stock-leave-30"></code></pre>
          <button type="button" data-copy-command="stock-leave-30">Copy 30-minute command</button>
          <p class="preset-test-note">
            This replaces the current temporary timeout with 30 minutes, then
            restores the saved primary tuple automatically.
          </p>
        </article>

        <article class="preset-test-card">
          <h3>Simple Repeater · scheduled primary radio</h3>
          <pre><code data-command="primary-cancel"></code></pre>
          <button type="button" data-copy-command="primary-cancel">Copy cancel commands</button>
          <p class="preset-test-note">
            Before the start, this removes the queued temporary schedule. While
            it is running, it also restores the saved primary tuple after the
            command reply drains.
          </p>
        </article>

        <article class="preset-test-card">
          <h3>Companion · before it starts</h3>
          <pre><code data-command="companion-cancel-before"></code></pre>
          <button type="button" data-copy-command="companion-cancel-before">Copy cancel commands</button>
        </article>

        <article class="preset-test-card">
          <h3>Companion · while it is running</h3>
          <h4>Leave now</h4>
          <pre><code data-command="companion-cancel-during"></code></pre>
          <button type="button" data-copy-command="companion-cancel-during">Copy restore commands</button>
          <p class="preset-test-note">
            If <code>get radio2.cross</code> was not <code>auto</code> before the
            test, restore that recorded value instead.
          </p>
          <h4>Leave in 30 minutes</h4>
          <pre><code data-command="companion-leave-30"></code></pre>
          <button type="button" data-copy-command="companion-leave-30">Copy 30-minute commands</button>
          <p class="preset-test-note">
            These commands clear any absolute second-profile schedule and give
            the test profile a fresh 30-minute lease. The saved second profile
            returns at expiry, but crossing remains <code>on</code>; afterward,
            restore the value you recorded before the test (<code>auto</code> is
            the normal default).
          </p>
        </article>
      </div>
    </section>

      <p class="preset-test-note">
        Primary scheduling requires a Simple Repeater build. Companion
        <code>tempradio2</code> and <code>tempradioat2</code> require dual-profile
        firmware. If a command is unknown, use only the method your installed
        firmware supports.
      </p>
      </div>
    </details>
    </div>
    <section aria-labelledby="share-title">
      <details class="preset-test-generator-disclosure" data-role="url-builder-disclosure" open>
        <summary>
          <h2 id="share-title">Build a temporary radio test URL</h2>
        </summary>
        <div class="preset-test-generator-disclosure-body">
          <p>
        Enter the test times in the selected time zone and choose the radio
        tuple. The generated URL converts the times to exact UTC instants, keeps
        the date punctuation readable, and keeps <code>tz</code> so the page
        displays them in the organizer's local time zone. With no URL settings,
        the builder starts at 5:00 PM tomorrow and ends at 5:00 PM two days
        later in the selected time zone, creating a 48-hour window.
          </p>

      <div class="preset-test-generator-layout">
        <form class="preset-test-generator" data-role="url-generator">
          <div class="preset-test-time-fields">
            <label>
              <span>Start date and time</span>
              <input type="datetime-local" name="start" step="60" required>
            </label>
            <label>
              <span>End date and time</span>
              <input type="datetime-local" name="end" step="60" required>
            </label>
          </div>

          <div class="preset-test-timezone-picker">
            <div class="preset-test-timezone-toolbar">
              <div>
                <span>Selected time zone</span>
                <strong data-role="selected-time-zone">Detecting browser time zone…</strong>
              </div>
              <button type="button" data-action="use-browser-time-zone">
                Use browser time zone
              </button>
            </div>
            <input type="hidden" name="tz" required>
            <div
              class="preset-test-timezone-map"
              data-role="timezone-map"
              aria-label="Interactive world map for selecting a time zone"
            ></div>
            <p class="preset-test-timezone-status" data-role="timezone-map-status" aria-live="polite">
              Loading time zone map…
            </p>
            <small>
              Click a region to select its IANA time zone. The initial selection
              comes from <code>tz=</code> when present; otherwise it uses your
              browser's time zone. Map design inspired by
              <a href="https://zones.arilyn.cc/" target="_blank" rel="noopener">zones.arilyn.cc</a>;
              boundaries from
              <a href="https://github.com/evansiroky/timezone-boundary-builder" target="_blank" rel="noopener">Timezone Boundary Builder</a>
              and © OpenStreetMap contributors.
            </small>
          </div>

          <div class="preset-test-radio-layout">
            <div class="preset-test-radio-fields">
            <div class="preset-test-generator-subheading">Test radio profile</div>
            <label>
              <span>Frequency (MHz)</span>
              <input type="number" name="freq" min="150" max="2500" step="0.001" required>
            </label>
            <label>
              <span>Bandwidth (kHz)</span>
              <select name="bw" required>
                <option>7.8</option><option>10.4</option><option>15.6</option>
                <option>20.8</option><option>31.25</option><option>41.7</option>
                <option>62.5</option><option>125</option><option>250</option><option>500</option>
              </select>
            </label>
            <label>
              <span>Spreading factor</span>
              <select name="sf" required>
                <option>5</option><option>6</option><option>7</option><option>8</option>
                <option>9</option><option>10</option><option>11</option><option>12</option>
              </select>
            </label>
            <label>
              <span>Coding-rate denominator</span>
              <select name="cr" required>
                <option>5</option><option>6</option><option>7</option><option>8</option>
              </select>
            </label>
            <label>
              <span>TX output for estimate (dBm)</span>
              <input type="number" name="tx" min="-30" max="60" step="0.1" required>
              <small>This estimate-only value does not change the TempRadio commands.</small>
            </label>
            </div>

            <aside class="preset-test-estimates" aria-labelledby="radio-estimates-title">
              <h3 id="radio-estimates-title">Radio estimates</h3>
              <dl>
                <div><dt>Nominal LoRa bitrate</dt><dd data-role="estimate-rate">—</dd></div>
                <div><dt>Estimated sensitivity</dt><dd data-role="estimate-sensitivity">—</dd></div>
                <div><dt>TX output used</dt><dd data-role="estimate-tx">—</dd></div>
                <div><dt>Estimated link budget</dt><dd data-role="estimate-budget">—</dd></div>
              </dl>
              <p class="preset-test-note">
                The bitrate is the nominal LoRa physical-layer rate; usable payload
                throughput is lower. Sensitivity assumes a 6 dB receiver noise figure
                and the standard LoRa SNR threshold for the selected spreading factor.
                Link budget is TX output minus that sensitivity, before antenna gain,
                cable loss, path loss, interference, and implementation differences.
              </p>
            </aside>
          </div>

          <button type="submit">Generate test URL</button>
        </form>
      </div>

      <p class="preset-test-error preset-test-generator-error"
         data-role="generator-error" role="alert" hidden></p>
      <pre class="preset-test-generated-url"><code data-command="generated-url"></code></pre>
      <div class="preset-test-generator-actions">
        <button type="button" data-copy-command="generated-url">Copy generated URL</button>
        <a data-role="open-generated-url" target="_blank" rel="noopener">Open generated page</a>
      </div>

          <p class="preset-test-note">
        Advanced use: <code>start</code> and <code>end</code> also accept ISO-8601
        timestamps with explicit UTC offsets or Unix epoch seconds. If
        <code>tz</code> is omitted, the page uses the browser's time zone. The
        other URL parameters are <code>freq</code>, <code>bw</code>,
        <code>sf</code>, <code>cr</code>, and estimate-only <code>tx</code>.
          </p>
        </div>
      </details>
    </section>
  </div>
</div>
