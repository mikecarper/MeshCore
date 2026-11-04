// On-chip multi-channel LoRa scanner (docs/pram-scanner.md): hop over a channel table running CAD
// on each channel, and stop on the first detection. With exit mode RX the chip is then already
// receiving the packet on that channel; the host just waits for RxDone as usual. Entry 1 does it
// as a blocking command, entry 4 in the background from the ROM's state handler slot.
//
// Channel table (written by the host with WriteRegMem32 before the command), at TAB:
//   struct { u32 freq_hz; u8 mod0, mod1, peak, pre; }   x n  (n <= 32)
//   mod0/mod1 = SetLoraModulationParams bytes ((sf << 4) | bw, (cr - 4) << 4 | ldro),
//   peak = CAD detect peak for that SF, pre = RX preamble length for that channel (0 = leave the
//   packet params alone). The RX preamble should match the transmitters' (MeshCore: 32 at SF7-8,
//   16 at SF9+); a mismatch gives header CRC errors.
//
// Entry 1 params (big-endian): n (1), cad symbols (1), exit mode (1: 0 = fallback, 1 = RX),
//   symbols to wait for a preamble after a detection (1), CAD flags (1), 0 (1), rounds (2).
//   CAD flags is byte 1 of SetLoraCadParams: bit 4 pbl_any (1 = detect only preamble symbols),
//   bits 3:0 pnr_delta (8 = fast CAD: end early when nothing is there). With bit 4 set, false
//   alarms all but vanish (docs/pram-scanner.md#cad-settings-hw). The wait is converted
//   per channel into RX-timeout ticks (32.768 kHz) from that channel's SF and bandwidth, so a slow
//   SF gets enough time and a false alarm on a fast one costs little.
// Response (8 bytes): channel index or 0xFF, round (2), CADs run (2), false alarms (1), polls of
//   the last CAD (2).
//
// False alarms: with exit mode RX, a detection is only reported once the receiver also sees a
// preamble. If the short RX timeout runs out first (the host sets StopTimeoutOnPreamble, so a
// real preamble cancels it), the chip falls back to FS and the scan carries on.
//
// The radio must be in LoRa mode, the fallback mode FS (0x0206 03) so the synthesizer keeps
// running between hops, and the DIO IRQ mask set by the host.
//
// Entry 4 runs the same scan in the background, with no command held: see bg_state below.
typedef unsigned int u32;
typedef unsigned char u8;

struct resp { u32 len; u8 *buf; };
typedef int (*handler_t)(u32 len, const u8 *p, struct resp *r);
#define H_SET_RF_FREQ ((handler_t)0xFC00)   // 0x0200
#define H_LORA_MOD    ((handler_t)0xF238)   // 0x0220
#define H_CAD_PARAMS  ((handler_t)0xF188)   // 0x0227
#define H_SET_CAD     ((handler_t)0xF178)   // 0x0228
#define H_LORA_PKT    ((handler_t)0xF274)   // 0x0221
#define H_SET_FS      ((handler_t)0xEB40)   // 0x0129

#define IRQ (*(volatile u32 *)0xF2A00C)     // IRQ status, write 1 to clear
#define IRQ_CAD_DET  (1u << 7)
#define IRQ_CAD_DONE (1u << 20)
#define IRQ_PREAMBLE (1u << 5)
#define IRQ_HDR      (1u << 6)
#define IRQ_RX_DONE  (1u << 18)
#define IRQ_TIMEOUT  (1u << 21)
#define IRQ_SCAN     ((1u << 5) | (1u << 6) | IRQ_CAD_DET | (1u << 18) | IRQ_CAD_DONE | (1u << 21) | (1u << 16))

// The ROM main loop (0xA1AC) takes the pending-event word set by interrupt handlers, runs host
// commands for bit 0 (that is where this code runs) and hands the other bits to the current
// state handler. CAD completion is processed there, so a command that spins blocks it. yield()
// does one iteration of that dispatch for the radio events, leaving bit 0 alone.
#define GP 0x806000u
#define EV_PENDING (*(volatile u32 *)(GP + 0x44))
#define EV_LEFT    (*(volatile u32 *)(GP + 0x6C))
#define STATE_FN   (*(u32 (**)(u32))(GP + 0x34))
#define NOTIFY_FN  (*(void (**)(u32))(GP + 0x38))
#define ROM_STATUS_UPDATE ((void (*)(void))0x25A4)

static void yield(void) {
  u32 st, ev;
  __asm__ volatile("clri %0" : "=r"(st) :: "memory");
  ev = EV_PENDING;
  EV_PENDING = ev & 1u;
  __asm__ volatile("seti %0" :: "r"(st) : "memory");
  ev &= ~1u;
  if (!ev) return;
  u32 (*h)(u32) = STATE_FN;
  if (h) ev = h(ev);
  if (ev & 0x4000u) NOTIFY_FN(256);
  EV_LEFT |= ev & 0xFFFF9FFFu;
  ROM_STATUS_UPDATE();                  // the loop refreshes the host-visible status after work
}

struct chan { u32 freq; u8 mod0, mod1, peak, pre; };

// RX timeout ticks (32.768 kHz) for `sym` symbols of this channel: sym * 2^sf / bw * 32768
static u32 wait_ticks(u32 mod0, u32 sym) {
  u32 sf = mod0 >> 4, bwc = mod0 & 0x0F, bw_div;
  switch (bwc) {
    case 2: bw_div = 31250; break;
    case 3: bw_div = 62500; break;
    case 4: bw_div = 125000; break;
    case 5: bw_div = 250000; break;
    case 6: bw_div = 500000; break;
    default: bw_div = 125000; break;
  }
  u32 chips = sym << sf;                              // <= 255 * 4096
  u32 q = chips / bw_div, rem = chips % bw_div;       // chips * 32768 / bw without overflowing 32 bits
  u32 t = (q << 15) + ((((rem << 8) / bw_div)) << 7) + 1;
  return t > 0xFFFFFFu ? 0xFFFFFFu : t;
}
#define TAB ((const struct chan *)0x807800)
#define POLL_MAX 200000u

int scan_cmd(u32 len, const u8 *p, struct resp *r) {
  if (len < 8) return 1;
  u32 n = p[0], nsym = p[1], exitm = p[2];
  u32 rounds = (p[6] << 8) | p[7];
  if (n == 0 || n > 32 || rounds == 0) return 1;
  u32 wsym = p[3];
  u8 cadp[7] = { (u8)nsym, p[4], (u8)exitm, 0, 0, 0, 0 };
  u8 fb[4], mod[2], pkt[4] = { 0, 0, 0, 0x02 };   // explicit header, CRC on, standard IQ
  u32 last_pre = 0x100, last_mod = 0xFFFFFFFFu, last_peak = 0x100, cads = 0, polls = 0, hit = 0xFF, rnd = 0, falses = 0;
  for (rnd = 0; rnd < rounds && hit == 0xFF; rnd++) {
    for (u32 c = 0; c < n; c++) {
      const struct chan *ch = &TAB[c];
      u32 f = ch->freq;
      fb[0] = f >> 24; fb[1] = f >> 16; fb[2] = f >> 8; fb[3] = f;
      H_SET_RF_FREQ(4, fb, 0);
      u32 m = ch->mod0 | (ch->mod1 << 8);
      if (m != last_mod) { mod[0] = ch->mod0; mod[1] = ch->mod1; H_LORA_MOD(2, mod, 0); last_mod = m; }
      if (ch->pre && ch->pre != last_pre) { pkt[1] = ch->pre; H_LORA_PKT(4, pkt, 0); last_pre = ch->pre; }
      if (ch->peak != last_peak) { cadp[6] = ch->peak; last_peak = ch->peak; }
      u32 wt = wait_ticks(ch->mod0, wsym);
      cadp[3] = wt >> 16; cadp[4] = wt >> 8; cadp[5] = wt;
      H_CAD_PARAMS(7, cadp, 0);
      IRQ = IRQ_SCAN;
      H_SET_CAD(0, 0, 0);
      cads++;
      u32 k = 0, s;
      while (!((s = IRQ) & IRQ_CAD_DONE)) {
        yield();
        if (++k > POLL_MAX) { hit = 0xFE; polls = k; goto out; }
      }
      polls = k;
      if (!(s & IRQ_CAD_DET)) continue;
      if (exitm != 1) { hit = c; goto out; }
      // CAD fired and the chip is now receiving on channel c: wait for a preamble or the timeout
      k = 0;
      while (!((s = IRQ) & (IRQ_PREAMBLE | IRQ_HDR | IRQ_RX_DONE | IRQ_TIMEOUT))) {
        yield();
        if (++k > POLL_MAX) { hit = 0xFD; goto out; }
      }
      if (s & (IRQ_PREAMBLE | IRQ_HDR | IRQ_RX_DONE)) { hit = c; goto out; }
      falses++;                         // timeout: false alarm, the chip is back in FS
      last_mod = 0xFFFFFFFFu;           // be safe: re-send modulation and packet params after an RX
      last_pre = 0x100;
    }
  }
out:
  r->buf[0] = hit;
  r->buf[1] = rnd >> 8; r->buf[2] = rnd;
  r->buf[3] = cads >> 8; r->buf[4] = cads;
  r->buf[5] = falses > 255 ? 255 : falses; r->buf[6] = polls >> 8; r->buf[7] = polls;
  r->len = 8;
  return 3;
}

// ---- Background scan (entry 4) ----
//
// The ROM main loop hands every radio event (CadDone, preamble, RX timeout, RxDone...) to the
// state handler at gp+0x34, which the ROM's own commands set (SetLoraCad installs 0x8074, a CAD
// detection in exit mode RX moves on to the RX handler 0x8248, and so on). bg_state takes that
// slot: it passes each event to the handler the ROM installed, then looks at the IRQ status and
// takes the next step of the scan itself. Commands and state handlers both run from the main
// loop, so nothing here races a host command, and between events the chip sleeps as usual.
//
//   CadDone, nothing      -> next channel
//   CadDone + detected    -> the chip is in RX on that channel: wait for a preamble
//   preamble / header     -> stay; RxDone then reaches the host's IRQ pin like any packet and
//                            the scan pauses ("parked") until the host has read it and resumes
//   RX timeout, header error -> false alarm, next channel
//
// The scan clears only its own IRQ bits, never RxDone / CrcError, which belong to the host.
// The host routes only RxDone to its DIO, so it sleeps through the hops.
#define IRQ_HDR_ERR  (1u << 9)
#define IRQ_CRC      (1u << 22)
#define IRQ_OWN      (IRQ_PREAMBLE | IRQ_HDR | IRQ_CAD_DET | IRQ_HDR_ERR | IRQ_CAD_DONE | IRQ_TIMEOUT)
#define IRQ_GATE     (*(volatile u32 *)0x80060C)   // global mask applied by 0x6ADC before the status
// The RX timeout (a channel of the radio's 32.768 kHz timer block). SetRx arms it through 0xC7F4;
// on a preamble the ROM stops it with 0xC828 when StopTimeoutOnPreamble is on (0x4C60). The
// armed flag is the byte at gp+12, and 0xC828 does nothing when it is clear.
#define ROM_RX_TIMEOUT_ARM  ((u32 (*)(u32))0xC7F4)
#define ROM_RX_TIMEOUT_STOP ((u32 (*)(void))0xC828)
enum { S_OFF, S_CAD, S_WAIT, S_RX, S_PARK };
struct bgs {
  u32 orig;                            // the ROM's state handler we stand in front of
  u32 running, state, n, c, wsym, gate_saved;
  u32 last_mod, last_pre, last_peak;
  u32 cads, falses, hdr_err, hits, rounds, last_hit, events, lost_irq;
  u8 cadp[8];
};
#define BG ((struct bgs *)0x807A00)    // after the channel table (32 x 8 bytes at 0x807800)
#define STATE_SLOT (*(volatile u32 *)(GP + 0x34))
#define RUNNING 0x5CA90001u                // RAM isn't cleared at load: a magic, not just nonzero

u32 bg_state(u32 ev);

static void hook_in(struct bgs *b) {
  u32 cur = STATE_SLOT;
  if (cur != (u32)bg_state) { b->orig = cur; STATE_SLOT = (u32)bg_state; }
}

static void start_cad(struct bgs *b) {
  const struct chan *ch = &TAB[b->c];
  u8 fb[4], mod[2], pkt[4] = { 0, 0, 0, 0x02 };
  u32 f = ch->freq;
  fb[0] = f >> 24; fb[1] = f >> 16; fb[2] = f >> 8; fb[3] = f;
  H_SET_RF_FREQ(4, fb, 0);
  u32 m = ch->mod0 | (ch->mod1 << 8);
  if (m != b->last_mod) { mod[0] = ch->mod0; mod[1] = ch->mod1; H_LORA_MOD(2, mod, 0); b->last_mod = m; }
  if (ch->pre && ch->pre != b->last_pre) { pkt[1] = ch->pre; H_LORA_PKT(4, pkt, 0); b->last_pre = ch->pre; }
  b->cadp[6] = ch->peak;
  u32 wt = wait_ticks(ch->mod0, b->wsym);
  b->cadp[3] = wt >> 16; b->cadp[4] = wt >> 8; b->cadp[5] = wt;
  H_CAD_PARAMS(7, b->cadp, 0);
  IRQ = IRQ_OWN;
  H_SET_CAD(0, 0, 0);
  b->cads++;
  b->state = S_CAD;
  hook_in(b);                          // SetLoraCad installed the ROM's CAD handler: get in front again
}

static void next_chan(struct bgs *b) {
  if (++b->c >= b->n) { b->c = 0; b->rounds++; }
  start_cad(b);
}

// Leave RX for the scan: a header error doesn't end RX, and SetLoraCad from RX starts nothing.
static void rx_abandon(struct bgs *b) {
  ROM_RX_TIMEOUT_STOP();
  H_SET_FS(0, 0, 0);
  b->last_mod = 0xFFFFFFFFu; b->last_pre = 0x100;   // re-send the params after an RX
  next_chan(b);
}

u32 bg_state(u32 ev) {
  struct bgs *b = BG;
  u32 (*h)(u32) = (u32 (*)(u32))b->orig;
  if (h) ev = h(ev);
  if (b->running != RUNNING) {         // stopped: step aside
    if (STATE_SLOT == (u32)bg_state) STATE_SLOT = b->orig;
    return ev;
  }
  hook_in(b);                          // the ROM handler may have installed its successor
  b->events++;
  u32 s = IRQ;
  switch (b->state) {
    case S_CAD:
      if (!(s & IRQ_CAD_DONE)) break;
      if (!(s & IRQ_CAD_DET)) { next_chan(b); break; }
      b->state = S_WAIT;
      /* fall through: the preamble may already be there */
    case S_WAIT:
      if (s & (IRQ_PREAMBLE | IRQ_HDR | IRQ_RX_DONE)) {
        b->state = S_RX;
        b->last_hit = b->c;
        // A preamble stopped the RX timeout. Re-arm it until the header: a preamble that never
        // becomes a header (noise, an aborted packet, another modulation) would otherwise leave
        // the radio in RX with no event to wake the scan.
        if (!(s & (IRQ_HDR | IRQ_RX_DONE))) {
          const struct chan *ch = &TAB[b->c];
          ROM_RX_TIMEOUT_ARM(wait_ticks(ch->mod0, (ch->pre ? ch->pre : 32) + 16));
        }
      } else {
        if (s & (IRQ_TIMEOUT | IRQ_HDR_ERR)) { b->falses++; rx_abandon(b); }
        break;
      }
      /* fall through */
    case S_RX:
      if (s & (IRQ_HDR | IRQ_RX_DONE)) ROM_RX_TIMEOUT_STOP();   // the header came: no limit on the payload
      if (s & IRQ_RX_DONE) { b->hits++; b->state = S_PARK; }
      else if (s & (IRQ_TIMEOUT | IRQ_HDR_ERR)) { b->hdr_err++; b->lost_irq = s; rx_abandon(b); }
      break;
    default:
      break;
  }
  return ev;
}

static void bg_resp(struct bgs *b, struct resp *r) {
  u32 v[10] = { b->state | (b->last_hit << 8) | (b->c << 16) | ((STATE_SLOT == (u32)bg_state) << 24),
                b->cads, b->falses, b->hdr_err, b->hits, b->rounds, b->events, b->lost_irq, IRQ, IRQ_GATE };
  for (u32 i = 0; i < 10; i++) {
    r->buf[4 * i] = v[i]; r->buf[4 * i + 1] = v[i] >> 8; r->buf[4 * i + 2] = v[i] >> 16; r->buf[4 * i + 3] = v[i] >> 24;
  }
  r->len = 40;
}

// 4: background scan. Params: op (1), then for op 1: n (1), cad symbols (1), symbols to wait for
// a preamble after a detection (1), optional CAD flags (1, as for entry 1).
//   op 1  start: scan the table from channel 0 and return at once (exit mode RX)
//   op 3  resume after a packet: clear RxDone / CrcError (the host has read the packet) and
//         carry on from the next channel
//   op 2  status
//   op 0  stop: give the state slot back; the chip stays in whatever the ROM left it (send
//         SetStandby next)
// Response, 10 LE words: state | last hit << 8 | channel << 16 | hooked << 24, CADs, false alarms,
// lost after a preamble (header error, or no header in time), packets, rounds, events seen, the
// IRQ status at the last loss, IRQ status, IRQ gate.
// Pure admission rule; tested on the host with all IRQ/pending cases.
static int lab_can_pause(u32 running, u32 state, u32 owns, u32 irq, u32 pending) {
  return running == RUNNING && state == S_CAD && owns &&
      !(irq & (IRQ_OWN | IRQ_RX_DONE | IRQ_CRC)) && !(pending & ~1u);
}

int bg_cmd(u32 len, const u8 *p, struct resp *r) {
  struct bgs *b = BG;
  if (len < 1) return 1;
  u32 op = p[0];
  if (op == 1) {
    if (len < 4 || p[1] == 0 || p[1] > 32) return 1;
    if (b->running != RUNNING || STATE_SLOT != (u32)bg_state) b->gate_saved = IRQ_GATE;  // not a restart
    IRQ_GATE |= IRQ_OWN;               // so the ROM records the scan's flags in the status
    b->n = p[1]; b->wsym = p[3]; b->c = 0;
    for (u32 i = 0; i < 8; i++) b->cadp[i] = 0;
    b->cadp[0] = p[2]; b->cadp[2] = 1; // exit mode RX
    if (len >= 5) b->cadp[1] = p[4];   // pbl_any << 4 | pnr_delta
    b->last_mod = 0xFFFFFFFFu; b->last_pre = b->last_peak = 0x100;
    b->cads = b->falses = b->hdr_err = b->hits = b->rounds = b->events = b->lost_irq = 0;
    b->last_hit = 0xFF;
    b->orig = 0;
    b->running = RUNNING;
    start_cad(b);
  } else if (op == 3) {
    if (b->running != RUNNING) return 1;
    IRQ = IRQ_RX_DONE | IRQ_CRC | IRQ_OWN;
    b->last_mod = 0xFFFFFFFFu; b->last_pre = 0x100;
    next_chan(b);
  } else if (op == 6) {
    // Lab extension: chip-side guarded handoff.  Normal CAD->RX processing
    // and host command dispatch share this main loop.  IRQ hardware may
    // still assert asynchronously, so inspect both IRQ flags and pending
    // events with interrupts fenced and conservatively defer uncertainty.
    // This cannot eliminate the normal race with a new RF arrival after the
    // handoff; the host must perform its usual channel-clear check before TX.
    u32 ps, stopped = 0;
    __asm__ volatile("clri %0" : "=r"(ps) :: "memory");
    if (lab_can_pause(b->running, b->state, STATE_SLOT == (u32)bg_state, IRQ, EV_PENDING)) {
      IRQ_GATE = b->gate_saved;
      b->running = 0;
      b->state = S_OFF;
      STATE_SLOT = b->orig;
      stopped = 1;
    }
    __asm__ volatile("seti %0" :: "r"(ps) : "memory");
    bg_resp(b, r);
    // Bit 7 of the status hooked byte is reserved for op6's result; the
    // response remains 40 bytes, matching the existing ROM response buffer.
    r->buf[3] |= (u8)(stopped ? 0x80u : 0u);
    return 3;
  } else if (op == 0) {
    if (b->running == RUNNING) IRQ_GATE = b->gate_saved;
    b->running = 0;
    b->state = S_OFF;
    if (STATE_SLOT == (u32)bg_state) STATE_SLOT = b->orig;
  } else if (op != 2) {
    return 1;
  }
  bg_resp(b, r);
  return 3;
}

// 6: lab-only host-timed config benchmark. No CAD/RX/TX is started here.
// Params: mode (0 control, 1 apply), freq(be32), mod0, mod1, preamble.
int bench_cmd(u32 len, const u8 *p, struct resp *r) {
  if (len != 8 || p[0] > 1) return 1;
  if (p[0]) {
    u8 pkt[4] = {0, p[7], 0, 0x02};
    if (H_SET_RF_FREQ(4, p+1, 0) != 2 || H_LORA_MOD(2, p+5, 0) != 2 || H_LORA_PKT(4, pkt, 0) != 2) return 0;
  }
  r->buf[0] = 0x42; r->len=1; return 3;
}

// 5: aux register access. Params: op (1: 0 read, 1 write), reg (4), value (4). Returns the value
// read after the write, LE.
int aux_cmd(u32 len, const u8 *p, struct resp *r) {
  if (len < 5) return 1;
  u32 a = ((u32)p[1] << 24) | (p[2] << 16) | (p[3] << 8) | p[4], v;
  if (p[0] == 1 && len >= 9) {
    v = ((u32)p[5] << 24) | (p[6] << 16) | (p[7] << 8) | p[8];
    __asm__ volatile("sr %0,[%1]" :: "r"(v), "r"(a) : "memory");
  }
  __asm__ volatile("lr %0,[%1]" : "=r"(v) : "r"(a));
  r->buf[0] = v; r->buf[1] = v >> 8; r->buf[2] = v >> 16; r->buf[3] = v >> 24;
  r->len = 4;
  return 3;
}
