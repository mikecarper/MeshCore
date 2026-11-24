// Appended to the existing source-extraction wrapper by radio_profile_load.py.
// This is an event/PHY fixture, not another implementation of the scan policy.
#include <algorithm>
#include <array>
#include <iostream>
#include <limits>
#include <vector>

namespace {
constexpr uint64_t Never = std::numeric_limits<uint64_t>::max();
enum Exposure { OffProfile=1, Retune=2, PacketHold=4, FifoHold=8, OwnTx=16,
                CpuDelay=32, UiDelay=64, StorageDelay=128 };
enum Kind { EndPacket, EndPreamble, Arrival, TxRequest, DelayStart, DelayEnd };
struct Frame {
  uint64_t start=0, preamble_end=0, end=0, acquire=Never;
  unsigned profile=0, bytes=0, preamble=0, exposure=0;
  uint64_t acquisition_us=0;
  bool collision=false, received=false, fixed_tx=false;
};
struct Event { uint64_t at; Kind kind; size_t id; };
struct Delay { uint64_t start, end; unsigned kind; };
struct Tx { uint64_t start, duration; unsigned profile; };
struct Window { uint64_t start, end; };

struct Simulation {
  RadioLibWrapper& w;
  uint64_t origin, duration, tx_end=Never, candidate_at=Never;
  uint64_t candidate_started=0, partial_listen_us=0, max_partial_listen_us=0;
  bool on=true, transmitting=false, fifo=false;
  int locked=-1, candidate=-1;
  unsigned profile=0, delay=0, tx_admitted=0, tx_deferred=0;
  unsigned service_calls=0, packet_guards=0, fifo_guards=0, tx_guards=0;
  unsigned partial_windows=0;
  uint64_t longest_service_gap=0, previous_service=0, max_packet_hold=0;
  std::vector<Frame> frames;
  std::vector<Event> events;
  std::vector<uint64_t> services;
  std::vector<Delay> delays;
  std::vector<Tx> requests;
  std::vector<Window> blackouts;
  std::vector<size_t> air;
  size_t next_event=0;

  explicit Simulation(RadioLibWrapper& wrapper, uint64_t length)
      : w(wrapper), origin(elapsed_us), duration(length) {}
  uint64_t now() const { return elapsed_us-origin; }

  void refreshCandidate() {
    if (!on || transmitting || fifo || locked>=0) {
      candidate=-1; candidate_at=Never; return;
    }
    if (candidate>=0) return; // Preserve continuous listening, not loop counts.
    for (size_t id: air) {
      const auto& f=frames[id];
      if (f.profile==profile && f.acquire==Never && now()<f.preamble_end) {
        candidate=int(id); candidate_started=now(); candidate_at=now()+f.acquisition_us; return;
      }
    }
  }

  void cancelCandidate() {
    if (candidate>=0 && now()>candidate_started) {
      const auto partial=now()-candidate_started;
      ++partial_windows; partial_listen_us+=partial;
      max_partial_listen_us=std::max(max_partial_listen_us,partial);
    }
    candidate=-1; candidate_at=Never;
  }
  void standby() {
    assert(locked<0 && !fifo && !transmitting);
    on=false; cancelCandidate();
  }
  void rx(unsigned active) {
    on=true; profile=active; refreshCandidate();
  }
  void expose(uint64_t until) {
    if (until==now()) return;
    for (size_t id: air) {
      auto& f=frames[id];
      if (now()>=f.preamble_end || f.acquire!=Never) continue;
      unsigned mask=0;
      if (transmitting) mask|=OwnTx;
      else if (!on) mask|=Retune;
      else if (profile!=f.profile) mask|=OffProfile;
      if (locked>=0 && locked!=int(id)) mask|=PacketHold;
      if (fifo) mask|=FifoHold;
      if (mask && delay) mask|=(delay==1 ? CpuDelay : delay==2 ? UiDelay : StorageDelay);
      f.exposure|=mask;
    }
  }

  void event(const Event& e) {
    if (e.kind==Arrival) {
      air.push_back(e.id);
    } else if (e.kind==EndPreamble) {
      if (candidate==int(e.id)) cancelCandidate();
    } else if (e.kind==EndPacket) {
      auto& f=frames[e.id];
      if (locked==int(e.id)) {
        assert(f.acquire>=f.start && f.acquire<=f.preamble_end);
        f.received=!f.collision;
        max_packet_hold=std::max(max_packet_hold, now()-f.acquire);
        locked=-1; w.packet=false; fifo=true; w.events.record();
      }
      air.erase(std::remove(air.begin(),air.end(),e.id),air.end());
    } else if (e.kind==TxRequest) {
      const auto& request=requests[e.id];
      // Use production TX admission/retune, never preempt an owned RX/FIFO.
      // Admission is attempted only outside a retune already on the stack.
      if (!on || transmitting) {
        ++tx_deferred;
      } else if (w.packet || w.hasPendingRadioInterrupt()) {
        if (w._profiles.enabled()) {
          const auto applies=w.applies;
          assert(w.prepareTransmitProfile(uint8_t(request.profile))==mesh::RadioParamApplyResult::BUSY);
          assert(w.applies==applies);
        }
        ++tx_deferred;
      } else {
        const auto result=w.prepareTransmitProfile(uint8_t(request.profile));
        if (result!=mesh::RadioParamApplyResult::APPLIED) { ++tx_deferred; return; }
        ++tx_admitted;
        transmitting=true; on=false; cancelCandidate();
        w.state=STATE_TX_WAIT;
        tx_end=now()+request.duration;
        blackouts.push_back({now(),tx_end});
      }
    } else if (e.kind==DelayStart) {
      assert(!delay); delay=delays[e.id].kind;
    } else {
      assert(delay==delays[e.id].kind); delay=0;
    }
  }

  void advance(uint64_t target) {
    // TX requests can invoke the extracted retune path, which advances time
    // recursively through the same queue while the radio is in standby.
    while (now()<=target) {
      const uint64_t next_static=next_event<events.size() ? events[next_event].at : Never;
      const uint64_t next=std::min({target,next_static,candidate_at,tx_end});
      assert(next>=now());
      expose(next); elapsed_us=origin+next;
      if (tx_end==next) {
        tx_end=Never; transmitting=false; w.state=STATE_IDLE; w.startRecv();
      }
      if (candidate_at==next) {
        assert(candidate>=0 && locked<0 && !fifo && on && !transmitting);
        locked=candidate; frames[size_t(locked)].acquire=now(); w.packet=true;
        candidate=-1; candidate_at=Never;
      }
      while (next_event<events.size() && events[next_event].at<=now()) {
        const auto current=events[next_event++];
        event(current);
      }
      refreshCandidate();
      if (now()>=target) break;
    }
  }

  void service() {
    const bool packet=w.packet, pending=w.hasPendingRadioInterrupt(), tx=transmitting;
    const unsigned applies=w.applies, switches=w._profiles.switches;
    longest_service_gap=std::max(longest_service_gap, now()-previous_service);
    previous_service=now(); ++service_calls;
    w.serviceProfileScan();
    if (packet || pending || tx) {
      assert(w.applies==applies && w._profiles.switches==switches);
      assert(w.packet==packet && w.hasPendingRadioInterrupt()==pending);
      packet_guards+=packet; fifo_guards+=pending; tx_guards+=tx;
    }
    // recvRaw/FIFO consumption is a hardware seam; service gaps postpone it.
    if (fifo) { assert(w.events.claim()); fifo=false; refreshCandidate(); }
  }

  void prepare() {
    for (size_t i=0;i<frames.size();++i) {
      auto& f=frames[i];
      const auto& p=w._profiles.params(uint8_t(f.profile));
      const double symbol=mesh::RadioProfiles::symbolUs(p);
      const int de=symbol>=16000 ? 1 : 0;
      const double groups=std::max(0.0,std::ceil((8.0*f.bytes-4*p.sf+(p.sf<=6?20:28)+16)
          /(4.0*(p.sf-2*de))));
      f.acquisition_us=uint64_t(std::ceil(4*symbol));
      f.preamble_end=f.start+uint64_t(std::ceil(f.preamble*symbol));
      f.end=f.start+uint64_t(std::ceil((f.preamble+(p.sf<=6?6.25:4.25)+8+groups*p.cr)*symbol));
      assert(f.preamble>=8 && f.bytes<=255 && f.profile<2);
      events.push_back({f.start,Arrival,i});
      events.push_back({f.preamble_end,EndPreamble,i});
      events.push_back({f.end,EndPacket,i});
    }
    for (size_t i=0;i<frames.size();++i) for (size_t j=i+1;j<frames.size();++j) {
      if (frames[i].profile==frames[j].profile && frames[i].start<frames[j].end
          && frames[j].start<frames[i].end) frames[i].collision=frames[j].collision=true;
    }
    for (size_t i=0;i<requests.size();++i) events.push_back({requests[i].start,TxRequest,i});
    for (size_t i=0;i<delays.size();++i) {
      events.push_back({delays[i].start,DelayStart,i});
      events.push_back({delays[i].end,DelayEnd,i});
    }
    std::sort(events.begin(),events.end(),[](const Event& a,const Event& b) {
      if (a.at!=b.at) return a.at<b.at;
      if (a.kind!=b.kind) return a.kind<b.kind;
      return a.id<b.id;
    });
  }

  void run() {
    prepare();
    advance(0);
    for (uint64_t at: services) {
      if (at<now()) continue; // Work performed in retunes coalesces missed loop slots.
      advance(at); service();
    }
    uint64_t tail=std::max(duration,now());
    for (const auto& f: frames) tail=std::max(tail,f.end);
    for (const auto& request: requests) tail=std::max(tail,request.start+request.duration+100000);
    for (const auto& gap: delays) tail=std::max(tail,gap.end+1000);
    // Drain every offered packet, including long frames ending after load time.
    while (now()<tail) { advance(std::min(tail,now()+1000)); if (!delay) service(); }
    assert(locked<0 && !fifo && !w.packet && !w.hasPendingRadioInterrupt());
    for (auto& f: frames) {
      uint64_t earliest=f.start;
      for (const auto& window: blackouts)
        if (window.start<f.end && window.end>f.start) earliest=std::max(earliest,window.end);
      f.fixed_tx=!f.collision && earliest+f.acquisition_us<=f.preamble_end;
      assert(!f.received || (!f.collision && f.fixed_tx && f.acquire!=Never));
    }
  }

  void print(unsigned switches) const {
    unsigned received=0, fixed=0, fixed_tx=0, collisions=0, raw_locks=0, crc_failures=0;
    for (const auto& f: frames) {
      received+=f.received; fixed+=!f.collision; fixed_tx+=f.fixed_tx; collisions+=f.collision;
      raw_locks+=f.acquire!=Never; crc_failures+=(f.acquire!=Never && f.collision);
    }
    std::cout << "{\"offered\":" << frames.size() << ",\"scanner_received\":" << received
        << ",\"scanner_lost\":" << frames.size()-received << ",\"fixed_continuous_received\":" << fixed
        << ",\"fixed_same_tx_received\":" << fixed_tx << ",\"collision_frames\":" << collisions
        << ",\"raw_modeled_locks\":" << raw_locks << ",\"raw_modeled_crc_failures\":" << crc_failures
        << ",\"partial_preamble_windows\":" << partial_windows
        << ",\"partial_preamble_listen_us\":" << partial_listen_us
        << ",\"max_partial_preamble_listen_us\":" << max_partial_listen_us
        << ",\"switches\":" << switches << ",\"service_calls\":" << service_calls
        << ",\"longest_service_gap_us\":" << longest_service_gap
        << ",\"max_packet_hold_us\":" << max_packet_hold
        << ",\"tx_admitted\":" << tx_admitted << ",\"tx_deferred\":" << tx_deferred
        << ",\"guard_checks\":{\"packet\":" << packet_guards << ",\"pending_irq\":" << fifo_guards
        << ",\"own_tx\":" << tx_guards << "},\"blackouts\":[";
    for (size_t i=0;i<blackouts.size();++i) {
      if (i) std::cout << ',';
      std::cout << '[' << blackouts[i].start << ',' << blackouts[i].end << ']';
    }
    std::cout << "],\"frames\":[";
    for (size_t i=0;i<frames.size();++i) {
      const auto& f=frames[i];
      if (i) std::cout << ',';
      std::cout << "{\"id\":" << i << ",\"profile\":" << f.profile << ",\"arrival_us\":" << f.start
          << ",\"preamble_end_us\":" << f.preamble_end << ",\"end_us\":" << f.end
          << ",\"payload_bytes\":" << f.bytes << ",\"preamble_symbols\":" << f.preamble
          << ",\"acquired_us\":";
      if (f.acquire==Never) std::cout << "null"; else std::cout << f.acquire;
      std::cout << ",\"exposure_mask\":" << f.exposure << ",\"collision\":" << f.collision
          << ",\"scanner_received\":" << f.received << ",\"fixed_same_tx_received\":" << f.fixed_tx
          << ",\"loss\":\"" << (f.received?"none":f.collision?"collision":"missed_preamble") << "\"}";
    }
    std::cout << "]}\n";
  }
};
Simulation* simulation=nullptr;
}

void simulationStandby() { if (simulation) simulation->standby(); }
void simulationRx(unsigned active) { if (simulation) simulation->rx(active); }
void simulationAdvance(unsigned delta) {
  if (simulation) simulation->advance(simulation->now()+delta); else elapsed_us+=delta;
}

int main() {
  uint64_t duration;
  unsigned dual, switch_us;
  if (!(std::cin>>duration>>dual>>switch_us)) return 2;
  RadioLibWrapper w;
  mesh::RadioProfileParams profiles[2];
  for (unsigned i=0;i<2;++i) {
    unsigned sf,cr,preamble;
    std::cin>>sf>>profiles[i].bw>>cr>>preamble;
    profiles[i].sf=uint8_t(sf); profiles[i].cr=uint8_t(cr); profiles[i].freq=909.5f+i;
    profiles[i].preamble=uint16_t(preamble);
    assert(mesh::RadioProfiles::valid(profiles[i]));
  }
  w._profiles.primary=profiles[0];
  w.cacheParams(profiles[0].freq,profiles[0].bw,profiles[0].sf,profiles[0].cr);
  w.switch_apply_us=switch_us;
  if (dual) {
    mesh::RadioProfileConfig secondary;
    secondary.params=profiles[1]; secondary.mode=mesh::RadioProfileMode::RxTx;
    w._profiles.setSecondary(secondary,false);
    w.serviceProfileScan();
    assert(w._profiles.switchTestReady());
    assert(w.tuneProfile(0)==mesh::RadioParamApplyResult::APPLIED);
  }
  w._nf_refresh_requested=false;
  Simulation s(w,duration);
  size_t count;
  std::cin>>count;
  s.frames.resize(count);
  for (auto& f:s.frames) std::cin>>f.start>>f.profile>>f.bytes>>f.preamble;
  std::cin>>count; s.services.resize(count);
  for (auto& at:s.services) std::cin>>at;
  std::cin>>count; s.delays.resize(count);
  for (auto& delay:s.delays) std::cin>>delay.start>>delay.end>>delay.kind;
  std::cin>>count; s.requests.resize(count);
  for (auto& request:s.requests) std::cin>>request.start>>request.duration>>request.profile;
  assert(std::cin);
  const auto initial_switches=w._profiles.switches;
  simulation=&s;
  s.run();
  simulation=nullptr;
  s.print(w._profiles.switches-initial_switches);
}
