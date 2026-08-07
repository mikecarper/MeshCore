(function (global) {
  "use strict";

  const BUNDLE_PREFIX = "MCPOLICY1:";
  const POLICY_FORMAT = "meshcore-policy-engine-playground";
  const POLICY_VERSION = 1;
  const PHASES = Object.freeze(["scope_gate", "rewrite", "forward", "content", "schedule"]);
  const PHASE_LABELS = Object.freeze({
    scope_gate: "Incoming scope gate",
    rewrite: "Scope rewrite",
    forward: "Forwarding decision",
    content: "Decrypted content",
    schedule: "Scheduling and retry",
  });
  const PHASE_ORDER = Object.freeze(Object.fromEntries(PHASES.map((phase, index) => [phase, index])));
  const OWNERS = Object.freeze(["scope", "filter", "admin", "system"]);
  const OWNER_LABELS = Object.freeze({
    scope: "ACL 4 scope manager",
    filter: "ACL 5 filter manager",
    admin: "administrator",
    system: "firmware system",
  });
  const MODES = Object.freeze(["active", "shadow", "disabled"]);
  const STOPS = Object.freeze(["none", "phase", "policy"]);
  const ROUTES = Object.freeze(["flood", "unscoped_flood", "scoped_flood"]);
  const TYPE_NAMES = Object.freeze([
    "req", "response", "txt_msg", "ack", "advert", "grp_txt", "grp_data", "anon_req",
    "path", "trace", "multipart", "control", "ota", "13", "14", "raw_custom",
  ]);
  const TYPE_CLASSES = Object.freeze(["class:group", "class:login", "class:other"]);
  const LOGIN_TYPES = Object.freeze(["req", "response", "txt_msg", "anon_req", "path"]);
  const GROUP_TYPES = Object.freeze(["grp_txt", "grp_data"]);
  const PATH_KINDS = Object.freeze([
    "none", "prefix", "blacklist",
    "bucket:1", "bucket:2", "bucket:3", "bucket:4", "bucket:5", "bucket:6",
    "loop:strict", "loop:moderate", "loop:minimal",
  ]);
  const PROFILE_BUDGETS = Object.freeze({
    stm32: 2048,
    nrf52: 8192,
    esp32: 16384,
    esp32_roomy: 65536,
  });

  class FilterToolError extends Error {
    constructor(message) {
      super(message);
      this.name = "FilterToolError";
    }
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function clean(value) {
    return String(value == null ? "" : value).trim();
  }

  function requiredText(value, label, maximum) {
    const text = clean(value);
    if (!text) throw new FilterToolError(`${label} is required.`);
    if (text.length > maximum) throw new FilterToolError(`${label} must be at most ${maximum} characters.`);
    if (/[\x00-\x1F\x7F]/.test(text)) throw new FilterToolError(`${label} contains a control character.`);
    return text;
  }

  function nullableInteger(value, minimum, maximum, label) {
    const text = clean(value);
    if (!text) return null;
    if (!/^\d+$/.test(text)) throw new FilterToolError(`${label} must be a whole number.`);
    const parsed = Number(text);
    if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
      throw new FilterToolError(`${label} must be ${minimum}-${maximum}.`);
    }
    return parsed;
  }

  function requiredInteger(value, minimum, maximum, label) {
    const parsed = nullableInteger(value, minimum, maximum, label);
    if (parsed == null) throw new FilterToolError(`${label} is required.`);
    return parsed;
  }

  function enumValue(value, allowed, label, fallback) {
    const normalized = clean(value).toLowerCase() || fallback;
    if (!allowed.includes(normalized)) throw new FilterToolError(`${label} is invalid.`);
    return normalized;
  }

  function normalizeRuleId(value) {
    const id = requiredText(value, "Rule ID", 32);
    if (!/^[A-Za-z][A-Za-z0-9_.-]{0,31}$/.test(id)) {
      throw new FilterToolError("Rule ID must begin with a letter and use only letters, digits, dot, dash, or underscore.");
    }
    return id;
  }

  function normalizeType(value) {
    const type = clean(value).toLowerCase() || "any";
    if (type === "any" || TYPE_NAMES.includes(type) || TYPE_CLASSES.includes(type)) return type;
    throw new FilterToolError("Payload matcher must be any, a known type, or class:group/login/other.");
  }

  function normalizePacketType(value) {
    const type = clean(value).toLowerCase();
    if (TYPE_NAMES.includes(type)) return type;
    throw new FilterToolError("Packet payload type must be one exact known type.");
  }

  function normalizeHops(value) {
    const hops = clean(value).toLowerCase() || "all";
    if (hops === "all" || hops === "0+") return "all";
    let match = hops.match(/^(\d{1,2})$/);
    if (match && Number(match[1]) <= 63) return String(Number(match[1]));
    match = hops.match(/^(\d{1,2})\+$/);
    if (match && Number(match[1]) <= 63) return Number(match[1]) === 0 ? "all" : `${Number(match[1])}+`;
    match = hops.match(/^(\d{1,2})-(\d{1,2})$/);
    if (match && Number(match[1]) <= Number(match[2]) && Number(match[2]) <= 63) {
      return `${Number(match[1])}-${Number(match[2])}`;
    }
    throw new FilterToolError("Received hops must be all, N, N+, or N-M using 0-63.");
  }

  function hopBounds(hops) {
    if (hops === "all") return [0, 63];
    if (hops.endsWith("+")) return [Number(hops.slice(0, -1)), 63];
    if (hops.includes("-")) return hops.split("-").map(Number);
    return [Number(hops), Number(hops)];
  }

  function normalizeChannel(value, optional) {
    const channel = clean(value);
    if (!channel) {
      if (optional) return "";
      throw new FilterToolError("Authenticated channel is required.");
    }
    if (channel.toLowerCase() === "public") return "public";
    if (channel[0] === "#") {
      if (channel.length < 2 || channel.length > 31 || /\s/.test(channel)) {
        throw new FilterToolError("A hashtag channel must be 1-30 non-space characters after #.");
      }
      return channel;
    }
    if (/^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{64})$/.test(channel)) return channel.toUpperCase();
    throw new FilterToolError("Channel must be public, #channel, or a 128/256-bit hexadecimal key.");
  }

  function normalizeScopeName(value) {
    let name = requiredText(value, "Scope name", 31);
    if (name[0] === "#") name = name.slice(1);
    if (!name || name.length > 30 || /\s/.test(name) || name[0] === "$") {
      throw new FilterToolError("Scope must be a public name of at most 30 non-space characters.");
    }
    return name;
  }

  function normalizeRegionName(value) {
    const name = requiredText(value, "Region name", 31).replace(/\?$/, "");
    if (name === "*") throw new FilterToolError("A wildcard region cannot be a rewrite target.");
    return name;
  }

  function normalizeIncoming(value) {
    const original = clean(value) || "any";
    const lowered = original.toLowerCase();
    const aliases = { "*": "any", n: "none", unscoped: "none", s: "scoped", a: "allowed", known: "allowed", u: "unknown" };
    if (Object.prototype.hasOwnProperty.call(aliases, lowered)) return aliases[lowered];
    if (["any", "none", "scoped", "allowed", "unknown"].includes(lowered)) return lowered;
    let match = original.match(/^(?:scope|s):(.+)$/i);
    if (match) return `scope:${normalizeScopeName(match[1])}`;
    match = original.match(/^(?:region|r):(.+)$/i);
    if (match) return `region:${normalizeRegionName(match[1])}`;
    throw new FilterToolError("Original scope must be any, none, scoped, allowed, unknown, scope:name, or region:name.");
  }

  function normalizePathPrefix(value, maximumHops) {
    const path = clean(value);
    if (!path || path === "*") return "";
    const ids = path.split(",").map((part) => part.trim().toUpperCase());
    if (ids.length < 1 || ids.length > maximumHops) {
      throw new FilterToolError(`Path accepts one to ${maximumHops} comma-separated pbyte IDs.`);
    }
    const width = ids[0].length;
    if (![2, 4, 6].includes(width) || ids.some((id) => id.length !== width || !/^[0-9A-F]+$/.test(id))) {
      throw new FilterToolError("Every path ID must use the same 2, 4, or 6 hex-character width.");
    }
    return ids.join(",");
  }

  function defaultRule(id) {
    return {
      id: id || "rule-1",
      phase: "rewrite",
      owner: "scope",
      priority: 100,
      mode: "active",
      stop: "none",
      route: "flood",
      type: "grp_data",
      hops: "all",
      channel: "#rgdata",
      incoming: "none",
      pathKind: "none",
      pathPrefix: "",
      sender: "",
      tempRadio: "any",
      verdict: "continue",
      scopeGate: "unchanged",
      targetKind: "scope",
      target: "BlackHole86",
      rate: null,
      burst: null,
      timing: "fast",
      queue: "inherit",
      retryBucket: "none",
      retryAttempts: null,
      tag: "",
    };
  }

  function hasAction(rule) {
    return rule.verdict === "drop"
      || rule.scopeGate !== "unchanged"
      || rule.targetKind !== "none"
      || rule.rate != null
      || rule.timing !== "inherit"
      || rule.queue !== "inherit"
      || rule.retryBucket !== "none"
      || Boolean(rule.tag)
      || rule.stop !== "none";
  }

  function normalizeRule(input) {
    if (!input || typeof input !== "object") throw new FilterToolError("Rule must be an object.");
    const rule = {
      id: normalizeRuleId(input.id),
      phase: enumValue(input.phase, PHASES, "Processing phase", "forward"),
      owner: enumValue(input.owner, OWNERS, "Rule owner", "filter"),
      priority: requiredInteger(input.priority == null ? 0 : input.priority, 0, 255, "Priority"),
      mode: enumValue(input.mode, MODES, "Rule mode", "active"),
      stop: enumValue(input.stop, STOPS, "Stop behavior", "none"),
      route: enumValue(input.route, ROUTES, "Route matcher", "flood"),
      type: normalizeType(input.type),
      hops: normalizeHops(input.hops),
      channel: normalizeChannel(input.channel, true),
      incoming: normalizeIncoming(input.incoming),
      pathKind: enumValue(input.pathKind, PATH_KINDS, "Path matcher", "none"),
      pathPrefix: "",
      sender: clean(input.sender),
      tempRadio: enumValue(input.tempRadio, ["any", "active", "inactive"], "Temporary-radio matcher", "any"),
      verdict: enumValue(input.verdict, ["continue", "drop"], "Forwarding verdict", "continue"),
      scopeGate: enumValue(input.scopeGate, ["unchanged", "require_allowed", "bypass_global"], "Scope-gate action", "unchanged"),
      targetKind: enumValue(input.targetKind, ["none", "scope", "region"], "Scope target", "none"),
      target: "",
      rate: nullableInteger(input.rate, 1, 65534, "Token rate"),
      burst: null,
      timing: enumValue(input.timing, ["inherit", "fast", "normal", "slow"], "Timing action", "inherit"),
      queue: enumValue(input.queue, ["inherit", "high", "normal", "low"], "Queue priority", "inherit"),
      retryBucket: enumValue(input.retryBucket, ["none", "bucket:1", "bucket:2", "bucket:3", "bucket:4", "bucket:5", "bucket:6"], "Retry bucket", "none"),
      retryAttempts: null,
      tag: clean(input.tag),
    };

    if (rule.pathKind === "prefix") rule.pathPrefix = normalizePathPrefix(input.pathPrefix, 3);
    if (rule.pathKind === "prefix" && !rule.pathPrefix) throw new FilterToolError("Prefix path matcher requires at least one pbyte ID.");
    if (rule.sender) {
      rule.sender = requiredText(rule.sender, "Sender", 31);
      if (rule.sender.includes(":")) throw new FilterToolError("Sender matcher cannot contain a colon.");
      if (!["content"].includes(rule.phase)) throw new FilterToolError("Decrypted sender matching is available only in the content phase.");
      if (!["any", "class:group", "grp_txt"].includes(rule.type)) {
        throw new FilterToolError("Sender matching requires type any, class:group, or grp_txt.");
      }
    }
    if (rule.channel && !["any", "class:group", "grp_txt", "grp_data"].includes(rule.type)) {
      throw new FilterToolError("Authenticated channel matching requires a group-capable payload type or class.");
    }
    if (rule.targetKind === "scope") rule.target = normalizeScopeName(input.target);
    if (rule.targetKind === "region") rule.target = normalizeRegionName(input.target);
    if (rule.rate != null) {
      rule.burst = nullableInteger(input.burst, 1, 65534, "Burst tokens");
      if (rule.burst == null) rule.burst = Math.max(1, rule.rate);
    } else if (clean(input.burst)) {
      throw new FilterToolError("Burst tokens require a token rate.");
    }
    if (rule.retryBucket !== "none") {
      rule.retryAttempts = nullableInteger(input.retryAttempts, 1, 10, "Retry attempts");
      if (rule.retryAttempts == null) rule.retryAttempts = 1;
    } else if (clean(input.retryAttempts)) {
      throw new FilterToolError("Retry attempts require a retry bucket.");
    }
    if (rule.tag) {
      rule.tag = requiredText(rule.tag, "Decision tag", 24);
      if (!/^[A-Za-z0-9_.-]+$/.test(rule.tag)) {
        throw new FilterToolError("Decision tag may use only letters, digits, dot, dash, or underscore.");
      }
    }

    if (rule.scopeGate !== "unchanged" && rule.phase !== "scope_gate") {
      throw new FilterToolError("Scope-gate actions must run in the incoming scope-gate phase.");
    }
    if (rule.targetKind !== "none" && rule.phase !== "rewrite") {
      throw new FilterToolError("Scope rewrite actions must run in the rewrite phase.");
    }
    if (rule.retryBucket !== "none" && rule.phase !== "schedule") {
      throw new FilterToolError("Retry actions must run in the scheduling phase.");
    }
    if (rule.owner === "scope") {
      if (!["scope_gate", "rewrite"].includes(rule.phase)) {
        throw new FilterToolError("ACL 4 rules may run only in scope-gate or rewrite phases.");
      }
      if (rule.verdict === "drop" || rule.rate != null || rule.queue !== "inherit" || rule.retryBucket !== "none") {
        throw new FilterToolError("ACL 4 cannot create general drop, rate, queue, or retry actions.");
      }
      if (rule.stop === "policy") throw new FilterToolError("ACL 4 may stop only its current phase.");
    }
    if (rule.owner === "filter") {
      if (rule.scopeGate !== "unchanged") throw new FilterToolError("ACL 5 cannot change the region-gate decision.");
      if (rule.targetKind === "region") throw new FilterToolError("ACL 5 cannot select a configured region target.");
    }
    if (!hasAction(rule)) throw new FilterToolError("Rule requires at least one action, tag, or stop behavior.");
    return rule;
  }

  function quoteDsl(value) {
    const text = String(value);
    if (!/[\s"'\\]/.test(text)) return text;
    return `"${text.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }

  function buildDefinition(input) {
    const rule = normalizeRule(input);
    const header = [
      "policy", "set", rule.id,
      `phase=${rule.phase}`,
      `owner=${rule.owner}`,
      `priority=${rule.priority}`,
    ];
    if (rule.mode !== "active") header.push(`mode=${rule.mode}`);
    const matches = [
      `route=${rule.route}`,
      `type=${rule.type}`,
      `hops=${rule.hops}`,
    ];
    if (rule.channel) matches.push(`channel=${quoteDsl(rule.channel)}`);
    if (rule.incoming !== "any") matches.push(`rx.scope=${quoteDsl(rule.incoming)}`);
    if (rule.pathKind === "prefix") matches.push(`path=prefix:${rule.pathPrefix}`);
    else if (rule.pathKind !== "none") matches.push(`path=${rule.pathKind}`);
    if (rule.sender) matches.push(`sender=${quoteDsl(rule.sender)}`);
    if (rule.tempRadio !== "any") matches.push(`tempradio=${rule.tempRadio}`);
    const actions = [];
    if (rule.verdict === "drop") actions.push("drop");
    if (rule.scopeGate === "require_allowed") actions.push("scope-gate=require");
    if (rule.scopeGate === "bypass_global") actions.push("scope-gate=bypass");
    if (rule.targetKind === "scope") actions.push(`scope=${quoteDsl(`#${rule.target}`)}`);
    if (rule.targetKind === "region") actions.push(`region=${quoteDsl(rule.target)}`);
    if (rule.rate != null) actions.push(`rate=${rule.rate}/min`, `burst=${rule.burst}`);
    if (rule.timing !== "inherit") actions.push(`timing=${rule.timing}`);
    if (rule.queue !== "inherit") actions.push(`queue=${rule.queue}`);
    if (rule.retryBucket !== "none") actions.push(`retry=${rule.retryBucket}/${rule.retryAttempts}`);
    if (rule.tag) actions.push(`tag=${rule.tag}`);
    if (rule.stop !== "none") actions.push(`stop=${rule.stop}`);
    return [...header, "when", ...matches, "do", ...actions].join(" ");
  }

  function tokenize(text) {
    const tokens = [];
    let current = "";
    let quote = "";
    let escaped = false;
    for (let index = 0; index < text.length; index += 1) {
      const character = text[index];
      if (quote) {
        if (escaped) {
          current += character;
          escaped = false;
        } else if (character === "\\") escaped = true;
        else if (character === quote) quote = "";
        else current += character;
      } else if (character === '"' || character === "'") {
        quote = character;
      } else if (/\s/.test(character)) {
        if (current) {
          tokens.push(current);
          current = "";
        }
      } else {
        current += character;
      }
    }
    if (quote || escaped) throw new FilterToolError("Definition contains an unterminated quote or escape.");
    if (current) tokens.push(current);
    return tokens;
  }

  function splitOption(token) {
    const equals = token.indexOf("=");
    return equals < 0 ? [token.toLowerCase(), ""] : [token.slice(0, equals).toLowerCase(), token.slice(equals + 1)];
  }

  function parseRate(value) {
    const match = clean(value).match(/^(\d+)(?:\/(?:min|m))?$/i);
    if (!match) throw new FilterToolError("Rate must use N/min.");
    return Number(match[1]);
  }

  function parseDefinition(definition) {
    const tokens = tokenize(clean(definition));
    if (tokens.length < 8 || tokens[0].toLowerCase() !== "policy" || tokens[1].toLowerCase() !== "set") {
      throw new FilterToolError("Readable definitions begin with: policy set <rule-id>.");
    }
    const whenIndex = tokens.findIndex((token) => token.toLowerCase() === "when");
    const doIndex = tokens.findIndex((token) => token.toLowerCase() === "do");
    if (whenIndex < 3 || doIndex <= whenIndex + 1 || doIndex === tokens.length - 1) {
      throw new FilterToolError("Definition requires header options, when matchers, and do actions.");
    }
    const rule = defaultRule(tokens[2]);
    rule.channel = "";
    rule.incoming = "any";
    rule.pathKind = "none";
    rule.pathPrefix = "";
    rule.sender = "";
    rule.tempRadio = "any";
    rule.verdict = "continue";
    rule.scopeGate = "unchanged";
    rule.targetKind = "none";
    rule.target = "";
    rule.rate = null;
    rule.burst = null;
    rule.timing = "inherit";
    rule.queue = "inherit";
    rule.retryBucket = "none";
    rule.retryAttempts = null;
    rule.tag = "";
    rule.stop = "none";

    tokens.slice(3, whenIndex).forEach((token) => {
      const [name, value] = splitOption(token);
      if (name === "phase") rule.phase = value;
      else if (name === "owner") rule.owner = value;
      else if (name === "priority") rule.priority = Number(value);
      else if (name === "mode") rule.mode = value;
      else throw new FilterToolError(`Unsupported rule-header option: ${token}`);
    });
    tokens.slice(whenIndex + 1, doIndex).forEach((token) => {
      const [name, value] = splitOption(token);
      if (name === "route") rule.route = value;
      else if (name === "type") rule.type = value;
      else if (name === "hops") rule.hops = value;
      else if (name === "channel") rule.channel = value;
      else if (name === "rx.scope") rule.incoming = value;
      else if (name === "path") {
        if (value.toLowerCase().startsWith("prefix:")) {
          rule.pathKind = "prefix";
          rule.pathPrefix = value.slice(7);
        } else rule.pathKind = value;
      } else if (name === "sender") rule.sender = value;
      else if (name === "tempradio") rule.tempRadio = value;
      else throw new FilterToolError(`Unsupported receive-time matcher: ${token}`);
    });
    tokens.slice(doIndex + 1).forEach((token) => {
      const [name, value] = splitOption(token);
      if (name === "drop") rule.verdict = "drop";
      else if (name === "scope-gate") {
        if (value.toLowerCase() === "require") rule.scopeGate = "require_allowed";
        else if (value.toLowerCase() === "bypass") rule.scopeGate = "bypass_global";
        else throw new FilterToolError("scope-gate action must be require or bypass.");
      } else if (name === "scope") {
        rule.targetKind = "scope";
        rule.target = value.replace(/^#/, "");
      } else if (name === "region") {
        rule.targetKind = "region";
        rule.target = value;
      } else if (name === "rate") rule.rate = parseRate(value);
      else if (name === "burst") rule.burst = Number(value);
      else if (name === "timing") rule.timing = value;
      else if (name === "queue") rule.queue = value;
      else if (name === "retry") {
        const match = value.match(/^(bucket:[1-6])\/(\d+)$/i);
        if (!match) throw new FilterToolError("Retry action must use bucket:N/attempts.");
        rule.retryBucket = match[1].toLowerCase();
        rule.retryAttempts = Number(match[2]);
      } else if (name === "tag") rule.tag = value;
      else if (name === "stop") rule.stop = value;
      else throw new FilterToolError(`Unsupported policy action: ${token}`);
    });
    return normalizeRule(rule);
  }

  function typeDescription(type) {
    const descriptions = {
      any: "any payload type",
      "class:group": "the group-text/data class",
      "class:login": "the login/admin payload class",
      "class:other": "the catch-all non-group, non-login class",
      req: "REQ", response: "RESPONSE", txt_msg: "TXT_MSG", ack: "ACK", advert: "ADVERT",
      grp_txt: "GRP_TXT", grp_data: "GRP_DATA", anon_req: "ANON_REQ", path: "PATH",
      trace: "TRACE", multipart: "MULTIPART", control: "CONTROL", ota: "OTA",
      "13": "reserved type 13", "14": "reserved type 14", raw_custom: "RAW_CUSTOM",
    };
    return descriptions[type] || type;
  }

  function routeDescription(route) {
    return {
      flood: "either flood route",
      unscoped_flood: "an unscoped flood",
      scoped_flood: "a transport-scoped flood",
    }[route];
  }

  function hopsDescription(hops) {
    if (hops === "all") return "at any received hop count";
    if (hops.endsWith("+")) return `at hop ${hops.slice(0, -1)} or higher`;
    if (hops.includes("-")) return `from hop ${hops.replace("-", " through ")}`;
    return `at exactly hop ${hops}`;
  }

  function channelDescription(channel) {
    if (channel === "public") return "authenticated Public channel";
    if (channel.startsWith("#")) return `authenticated ${channel}`;
    return `authenticated key ${channel.slice(0, 8)}...`;
  }

  function actionPhrases(rule) {
    const phrases = [];
    if (rule.verdict === "drop") phrases.push("make the forwarding verdict drop (sticky)");
    if (rule.scopeGate === "require_allowed") phrases.push("require the original incoming scope to resolve to an allowed region");
    if (rule.scopeGate === "bypass_global") phrases.push("bypass the global region gate for this packet");
    if (rule.targetKind === "scope") phrases.push(`select regionless scope #${rule.target}`);
    if (rule.targetKind === "region") phrases.push(`select configured region ${rule.target}`);
    if (rule.rate != null) phrases.push(`attach a ${rule.rate}/minute token bucket with burst ${rule.burst}`);
    if (rule.timing !== "inherit") phrases.push(`select ${rule.timing} timing`);
    if (rule.queue !== "inherit") phrases.push(`select ${rule.queue} queue priority`);
    if (rule.retryBucket !== "none") phrases.push(`retry through ${rule.retryBucket} up to ${rule.retryAttempts} time(s)`);
    if (rule.tag) phrases.push(`attach decision tag ${rule.tag}`);
    if (rule.stop === "phase") phrases.push("stop later rules in this phase");
    if (rule.stop === "policy") phrases.push("stop all later configurable policy phases");
    return phrases;
  }

  function explainRule(input) {
    const rule = normalizeRule(input);
    const conditions = [routeDescription(rule.route), typeDescription(rule.type), hopsDescription(rule.hops)];
    if (rule.channel) conditions.push(channelDescription(rule.channel));
    if (rule.incoming === "none") conditions.push("an originally unscoped packet");
    else if (rule.incoming === "scoped") conditions.push("an originally scoped packet");
    else if (rule.incoming === "allowed") conditions.push("an original scope allowed by the region map");
    else if (rule.incoming === "unknown") conditions.push("an original scope that is unknown or denied");
    else if (rule.incoming.startsWith("scope:")) conditions.push(`original exact scope #${rule.incoming.slice(6)}`);
    else if (rule.incoming.startsWith("region:")) conditions.push(`original exact region ${rule.incoming.slice(7)}`);
    if (rule.pathKind === "prefix") conditions.push(`a path beginning ${rule.pathPrefix}`);
    else if (rule.pathKind === "blacklist") conditions.push("a path matching the passive blacklist");
    else if (rule.pathKind.startsWith("bucket:")) conditions.push(`a path matching ${rule.pathKind}`);
    else if (rule.pathKind.startsWith("loop:")) conditions.push(`the ${rule.pathKind.slice(5)} own-ID loop threshold`);
    if (rule.sender) conditions.push(`decrypted sender "${rule.sender}"`);
    if (rule.tempRadio !== "any") conditions.push(`temporary radio ${rule.tempRadio}`);
    const mode = rule.mode === "shadow"
      ? "In shadow mode, report that it would "
      : rule.mode === "disabled"
        ? "This rule is disabled; if enabled it would "
        : "When matched, ";
    return `${PHASE_LABELS[rule.phase]} rule ${rule.id}, owned by ${OWNER_LABELS[rule.owner]}, matches ${conditions.join(", ")}. ${mode}${actionPhrases(rule).join(" and ")}. Priority ${rule.priority}; every condition reads immutable receive-time facts.`;
  }

  function limitsRemoteManagement(rule) {
    const matchesLoginType = rule.type === "any"
      || rule.type === "class:login"
      || LOGIN_TYPES.includes(rule.type);
    const canRestrict = rule.verdict === "drop"
      || rule.rate != null
      || rule.scopeGate === "require_allowed";
    return !rule.channel && matchesLoginType && canRestrict;
  }

  function ruleWarnings(input) {
    const rule = normalizeRule(input);
    const warnings = [];
    const [minimumHops] = hopBounds(rule.hops);
    if (rule.verdict === "drop" && rule.route === "flood" && rule.type === "any"
        && rule.hops === "all" && !rule.channel && rule.incoming === "any"
        && rule.pathKind === "none" && rule.tempRadio === "any") {
      warnings.push("This is a global drop rule across both flood route types.");
    }
    if (limitsRemoteManagement(rule)) {
      const zeroHop = minimumHops === 0 && rule.pathKind === "none";
      warnings.push(zeroHop
        ? "Remote-management relay risk: this rule can restrict zero-hop login/admin floods. Direct routes and local delivery stay outside this policy, but multi-hop login reach can still be lost."
        : "Remote-management reach warning: this rule intentionally limits relayed login/admin floods at its hop or path condition. Direct routes and local delivery stay outside this policy.");
    }
    if (rule.type === "class:other") warnings.push("class:other intentionally includes current and future types outside group and login classes, including OTA.");
    if (rule.channel && rule.type === "any") warnings.push("A channel condition narrows type=any to authenticated group text/data packets.");
    if (rule.sender) warnings.push("Displayed sender names are spoofable and are moderation signals, not identities.");
    if (rule.pathKind !== "none") warnings.push("Pbyte and path-table matches use truncated routing hints, not authenticated identities.");
    if (rule.pathKind.startsWith("bucket:")) warnings.push("The selected bucket must exist on the target node; the policy stores a reference, not its IDs.");
    if (rule.stop === "policy") warnings.push("stop=policy skips every later configurable phase when this rule matches, but not mandatory protocol/radio safety.");
    if (rule.mode === "shadow") warnings.push("Shadow mode records the match but applies no action and does not stop processing.");
    if (rule.targetKind === "region") warnings.push("The configured region must exist, allow flooding, and have a usable transport key at evaluation time.");
    return warnings;
  }

  function estimateRuleBytes(input) {
    const rule = normalizeRule(input);
    let bytes = 12;
    bytes += 3 + 3 + 3;
    if (rule.channel) bytes += 3 + (rule.channel.length === 32 || rule.channel.length === 64 ? rule.channel.length / 2 : rule.channel.length);
    if (rule.incoming !== "any") bytes += 3 + rule.incoming.length;
    if (rule.pathKind === "prefix") bytes += 4 + rule.pathPrefix.replace(/,/g, "").length / 2;
    else if (rule.pathKind !== "none") bytes += 3;
    if (rule.sender) bytes += 3 + rule.sender.length;
    if (rule.tempRadio !== "any") bytes += 3;
    if (rule.verdict === "drop") bytes += 2;
    if (rule.scopeGate !== "unchanged") bytes += 3;
    if (rule.targetKind !== "none") bytes += 3 + rule.target.length;
    if (rule.rate != null) bytes += 7;
    if (rule.timing !== "inherit") bytes += 3;
    if (rule.queue !== "inherit") bytes += 3;
    if (rule.retryBucket !== "none") bytes += 4;
    if (rule.tag) bytes += 3 + rule.tag.length;
    if (rule.stop !== "none") bytes += 3;
    return Math.ceil(bytes);
  }

  function sortedRules(rules) {
    return rules.slice().sort((left, right) => {
      const phase = PHASE_ORDER[left.phase] - PHASE_ORDER[right.phase];
      if (phase !== 0) return phase;
      if (left.priority !== right.priority) return right.priority - left.priority;
      if (left.id < right.id) return -1;
      if (left.id > right.id) return 1;
      return 0;
    });
  }

  function policyWarnings(rules, profile) {
    const warnings = [];
    const ids = new Set();
    rules.forEach((rule) => {
      if (ids.has(rule.id)) warnings.push(`Rule ID ${rule.id} is duplicated.`);
      ids.add(rule.id);
    });
    const activeRewrites = rules.filter((rule) => rule.mode === "active" && rule.targetKind !== "none");
    activeRewrites.forEach((rule, index) => {
      activeRewrites.slice(index + 1).forEach((other) => {
        if (rule.phase === other.phase && rule.priority === other.priority
            && rule.route === other.route && rule.type === other.type
            && rule.hops === other.hops && rule.channel === other.channel) {
          warnings.push(`Rewrite rules ${rule.id} and ${other.id} have overlapping core matches at the same priority; stable ID decides the winner.`);
        }
      });
    });
    const estimated = rules.reduce((total, rule) => total + estimateRuleBytes(rule), 16);
    const managementRules = rules.filter((rule) => rule.mode === "active" && limitsRemoteManagement(rule));
    if (managementRules.length) {
      warnings.push(`${managementRules.length} active rule${managementRules.length === 1 ? "" : "s"} can limit relayed remote-login traffic. Direct routes and local packet delivery remain outside the policy, but end-to-end flood login reach is not guaranteed.`);
    }
    const budget = PROFILE_BUDGETS[profile] || PROFILE_BUDGETS.nrf52;
    if (estimated > budget) warnings.push(`Approximate packed size ${estimated} bytes exceeds the selected ${budget}-byte target budget.`);
    if (rules.length > 255) warnings.push("The draft exceeds the proposed 255 stable rule-ID limit.");
    return Array.from(new Set(warnings));
  }

  function utf8ToBase64(text) {
    if (typeof Buffer !== "undefined") return Buffer.from(text, "utf8").toString("base64");
    const bytes = new TextEncoder().encode(text);
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    return global.btoa(binary);
  }

  function base64ToUtf8(encoded) {
    try {
      if (typeof Buffer !== "undefined") return Buffer.from(encoded, "base64").toString("utf8");
      const binary = global.atob(encoded);
      return new TextDecoder().decode(Uint8Array.from(binary, (character) => character.charCodeAt(0)));
    } catch (_error) {
      throw new FilterToolError("Playground bundle is not valid Base64.");
    }
  }

  function policyDocument(rules) {
    return {
      format: POLICY_FORMAT,
      version: POLICY_VERSION,
      status: "design-preview",
      evaluation: "phase, descending priority, stable rule ID; immutable receive-time matches",
      rules: sortedRules(rules.map(normalizeRule)),
    };
  }

  function encodeBundle(rules) {
    return BUNDLE_PREFIX + utf8ToBase64(JSON.stringify(policyDocument(rules)));
  }

  function decodePolicyObject(value) {
    if (!value || typeof value !== "object" || value.format !== POLICY_FORMAT || value.version !== POLICY_VERSION) {
      throw new FilterToolError("Policy document has an unknown format or version.");
    }
    if (!Array.isArray(value.rules)) throw new FilterToolError("Policy document does not contain a rules array.");
    return value.rules.map(normalizeRule);
  }

  function decodeBundle(value) {
    const text = clean(value).replace(/\s+/g, "");
    if (!text.startsWith(BUNDLE_PREFIX)) throw new FilterToolError(`Bundle must begin with ${BUNDLE_PREFIX}`);
    try {
      return decodePolicyObject(JSON.parse(base64ToUtf8(text.slice(BUNDLE_PREFIX.length))));
    } catch (error) {
      if (error instanceof FilterToolError) throw error;
      throw new FilterToolError("Playground bundle does not contain valid policy JSON.");
    }
  }

  function parsePolicyInput(input) {
    const text = clean(input)
      .replace(/^```(?:text|json)?\s*/i, "")
      .replace(/```\s*$/, "")
      .trim();
    if (!text) throw new FilterToolError("Paste readable policy, JSON, or a playground bundle first.");
    if (text.startsWith(BUNDLE_PREFIX)) return decodeBundle(text);
    if (text[0] === "{") {
      try {
        return decodePolicyObject(JSON.parse(text));
      } catch (error) {
        if (error instanceof FilterToolError) throw error;
        throw new FilterToolError("Policy JSON could not be parsed.");
      }
    }
    const rules = [];
    const failures = [];
    text.split(/\r?\n/).forEach((rawLine, index) => {
      const line = clean(rawLine);
      if (!line || line.startsWith("#")) return;
      try {
        rules.push(parseDefinition(line));
      } catch (error) {
        failures.push(`Line ${index + 1}: ${error.message}`);
      }
    });
    if (failures.length) throw new FilterToolError(failures.join(" "));
    if (!rules.length) throw new FilterToolError("No readable policy rules were found.");
    return rules;
  }

  function normalizePacketPath(value) {
    return normalizePathPrefix(value, 63);
  }

  function normalizePacket(input) {
    const scopeStatus = enumValue(input.scopeStatus, ["none", "allowed", "unknown"], "Packet scope status", "none");
    const type = normalizePacketType(input.type);
    const channel = clean(input.channel) ? normalizeChannel(input.channel, false) : "";
    const sender = clean(input.sender);
    if (channel && !GROUP_TYPES.includes(type)) {
      throw new FilterToolError("Only group text/data packet facts can include an authenticated channel.");
    }
    if (sender && type !== "grp_txt") {
      throw new FilterToolError("Only a decrypted group-text packet can include a displayed sender.");
    }
    const buckets = clean(input.buckets)
      ? clean(input.buckets).split(",").map((value) => requiredInteger(value, 1, 6, "Path bucket"))
      : [];
    return {
      route: enumValue(input.route, ["unscoped_flood", "scoped_flood"], "Packet route", "unscoped_flood"),
      type,
      hops: requiredInteger(input.hops, 0, 63, "Packet hops"),
      channel,
      path: normalizePacketPath(input.path),
      scopeStatus,
      scopeName: clean(input.scopeName).replace(/^#/, ""),
      regionName: clean(input.regionName),
      sender,
      tempRadio: Boolean(input.tempRadio),
      blacklist: Boolean(input.blacklist),
      buckets: Array.from(new Set(buckets)),
      loopLevel: requiredInteger(input.loopLevel == null ? 0 : input.loopLevel, 0, 3, "Loop result"),
    };
  }

  function typeMatches(ruleType, packetType) {
    if (ruleType === "any") return true;
    if (ruleType === "class:group") return GROUP_TYPES.includes(packetType);
    if (ruleType === "class:login") return LOGIN_TYPES.includes(packetType);
    if (ruleType === "class:other") return !GROUP_TYPES.includes(packetType) && !LOGIN_TYPES.includes(packetType);
    return ruleType === packetType;
  }

  function incomingMatches(ruleIncoming, packet) {
    if (ruleIncoming === "any") return true;
    if (ruleIncoming === "none") return packet.scopeStatus === "none";
    if (ruleIncoming === "scoped") return packet.scopeStatus !== "none";
    if (ruleIncoming === "allowed") return packet.scopeStatus === "allowed";
    if (ruleIncoming === "unknown") return packet.scopeStatus === "unknown";
    if (ruleIncoming.startsWith("scope:")) return packet.scopeStatus !== "none" && packet.scopeName === ruleIncoming.slice(6);
    if (ruleIncoming.startsWith("region:")) {
      return packet.scopeStatus === "allowed"
        && packet.regionName.toLowerCase() === ruleIncoming.slice(7).toLowerCase();
    }
    return false;
  }

  function matchRule(inputRule, inputPacket) {
    const rule = normalizeRule(inputRule);
    const packet = normalizePacket(inputPacket);
    const misses = [];
    if (rule.route === "flood" && !["unscoped_flood", "scoped_flood"].includes(packet.route)) misses.push("route is not flood");
    else if (rule.route !== "flood" && rule.route !== packet.route) misses.push(`route is ${packet.route}`);
    if (!typeMatches(rule.type, packet.type)) misses.push(`payload type ${packet.type} is outside ${rule.type}`);
    const [minimum, maximum] = hopBounds(rule.hops);
    if (packet.hops < minimum || packet.hops > maximum) misses.push(`hop ${packet.hops} is outside ${rule.hops}`);
    if (rule.channel && rule.channel !== packet.channel) misses.push("authenticated channel differs or is unavailable");
    if (!incomingMatches(rule.incoming, packet)) misses.push(`original scope does not satisfy ${rule.incoming}`);
    if (rule.pathKind === "prefix") {
      const wanted = rule.pathPrefix.split(",");
      const actual = packet.path ? packet.path.split(",") : [];
      if (wanted.some((id, index) => actual[index] !== id)) misses.push(`path does not begin ${rule.pathPrefix}`);
    } else if (rule.pathKind === "blacklist" && !packet.blacklist) misses.push("passive blacklist did not match");
    else if (rule.pathKind.startsWith("bucket:") && !packet.buckets.includes(Number(rule.pathKind.slice(7)))) {
      misses.push(`${rule.pathKind} did not match`);
    } else if (rule.pathKind.startsWith("loop:")) {
      const required = { strict: 1, moderate: 2, minimal: 3 }[rule.pathKind.slice(5)];
      if (packet.loopLevel < required) misses.push(`${rule.pathKind} threshold was not reached`);
    }
    if (rule.sender && rule.sender.toLowerCase() !== packet.sender.toLowerCase()) misses.push("decrypted sender differs or is unavailable");
    if (rule.tempRadio === "active" && !packet.tempRadio) misses.push("temporary radio is inactive");
    if (rule.tempRadio === "inactive" && packet.tempRadio) misses.push("temporary radio is active");
    return { matched: misses.length === 0, misses };
  }

  function applyRuleActions(rule, packet, decision) {
    const applied = [];
    if (rule.verdict === "drop") {
      decision.drop = true;
      applied.push("sticky drop");
    }
    if (rule.scopeGate !== "unchanged" && decision.scopeGate === "global") {
      decision.scopeGate = rule.scopeGate;
      applied.push(rule.scopeGate === "require_allowed" ? "require allowed original scope" : "bypass global region gate");
      if (rule.scopeGate === "require_allowed" && packet.scopeStatus !== "allowed") {
        decision.drop = true;
        applied.push("scope requirement failed -> sticky drop");
      }
    } else if (rule.scopeGate !== "unchanged") applied.push("scope-gate action ignored; higher rule already selected it");
    if (rule.targetKind !== "none" && !decision.scopeTarget) {
      decision.scopeTarget = { kind: rule.targetKind, name: rule.target, rule: rule.id };
      applied.push(`${rule.targetKind} target ${rule.target}`);
    } else if (rule.targetKind !== "none") applied.push("rewrite ignored; higher rule already selected a target");
    if (rule.rate != null) {
      decision.rates.push({ rule: rule.id, rate: rule.rate, burst: rule.burst });
      applied.push(`rate ${rule.rate}/min burst ${rule.burst}`);
    }
    if (rule.timing !== "inherit" && decision.timing === "inherit") {
      decision.timing = rule.timing;
      applied.push(`${rule.timing} timing`);
    } else if (rule.timing !== "inherit") applied.push("timing ignored; higher rule already selected it");
    if (rule.queue !== "inherit" && decision.queue === "inherit") {
      decision.queue = rule.queue;
      applied.push(`${rule.queue} queue`);
    } else if (rule.queue !== "inherit") applied.push("queue action ignored; higher rule already selected it");
    if (rule.retryBucket !== "none" && !decision.retry) {
      decision.retry = { bucket: rule.retryBucket, attempts: rule.retryAttempts, rule: rule.id };
      applied.push(`retry ${rule.retryBucket}/${rule.retryAttempts}`);
    } else if (rule.retryBucket !== "none") applied.push("retry ignored; higher rule already selected it");
    if (rule.tag) {
      decision.tags.push(rule.tag);
      applied.push(`tag ${rule.tag}`);
    }
    return applied;
  }

  function simulatePolicy(inputRules, inputPacket) {
    const rules = sortedRules(inputRules.map(normalizeRule));
    const packet = normalizePacket(inputPacket);
    const decision = {
      drop: false,
      scopeGate: "global",
      scopeTarget: null,
      rates: [],
      timing: "inherit",
      queue: "inherit",
      retry: null,
      tags: [],
    };
    const trace = [];
    const stoppedPhases = new Set();
    let policyStopped = false;
    rules.forEach((rule) => {
      if (rule.mode === "disabled") {
        trace.push({ id: rule.id, phase: rule.phase, status: "disabled", detail: "Rule is disabled." });
        return;
      }
      if (policyStopped) {
        trace.push({ id: rule.id, phase: rule.phase, status: "stopped", detail: "A prior stop=policy skipped this rule." });
        return;
      }
      if (stoppedPhases.has(rule.phase)) {
        trace.push({ id: rule.id, phase: rule.phase, status: "stopped", detail: "A prior stop=phase skipped this rule." });
        return;
      }
      const result = matchRule(rule, packet);
      if (!result.matched) {
        trace.push({ id: rule.id, phase: rule.phase, status: "miss", detail: result.misses.join("; ") });
        return;
      }
      if (rule.mode === "shadow") {
        trace.push({ id: rule.id, phase: rule.phase, status: "shadow", detail: `Would apply: ${actionPhrases(rule).join("; ")}.` });
        return;
      }
      const applied = applyRuleActions(rule, packet, decision);
      trace.push({ id: rule.id, phase: rule.phase, status: "match", detail: `Applied: ${applied.join("; ")}.` });
      if (rule.stop === "phase") stoppedPhases.add(rule.phase);
      if (rule.stop === "policy") policyStopped = true;
    });
    return { packet, decision, trace };
  }

  function documentedDropRule(id, type, hops, priority) {
    return {
      ...defaultRule(id),
      phase: "forward",
      owner: "filter",
      priority: priority == null ? 100 : priority,
      type,
      hops,
      channel: "",
      incoming: "any",
      targetKind: "none",
      target: "",
      timing: "inherit",
      verdict: "drop",
    };
  }

  const EXAMPLES = Object.freeze({
    channel_scope: Object.freeze([
      {
        ...defaultRule("rgdata-scope"),
        type: "class:group",
        channel: "#rgdata",
        incoming: "any",
        priority: 100,
      },
    ]),
    blackhole: Object.freeze([
      {
        ...defaultRule("blackhole-after-hop-3"),
        type: "grp_data",
        hops: "4+",
        channel: "#rgdata",
        incoming: "none",
        priority: 100,
      },
    ]),
    scope_rewrite: Object.freeze([
      {
        ...defaultRule("usa-to-blackhole"),
        type: "grp_data",
        channel: "#rgdata",
        incoming: "scope:usa",
        priority: 100,
      },
    ]),
    prefix_rate: Object.freeze([
      {
        ...documentedDropRule("prefix-860c-rate", "any", "all"),
        pathKind: "prefix",
        pathPrefix: "860C",
        verdict: "continue",
        rate: 10,
        burst: 10,
      },
    ]),
    channel_stop: Object.freeze([
      {
        ...documentedDropRule("rgdata-short-hop-stop", "grp_data", "0-2", 200),
        channel: "#rgdata",
        verdict: "continue",
        stop: "policy",
      },
    ]),
    high_traffic: Object.freeze([
      documentedDropRule("limit-req", "req", "3+"),
      documentedDropRule("limit-response", "response", "9+"),
      documentedDropRule("limit-group-data", "grp_data", "3+"),
      documentedDropRule("limit-anon-request", "anon_req", "9+"),
      documentedDropRule("limit-path", "path", "9+"),
      documentedDropRule("limit-control", "control", "1+"),
    ]),
    moderation: Object.freeze([
      {
        ...defaultRule("public-noisy-user"),
        phase: "content",
        owner: "filter",
        type: "grp_txt",
        channel: "public",
        incoming: "any",
        sender: "Noisy User",
        targetKind: "none",
        target: "",
        timing: "inherit",
        rate: 5,
        burst: 5,
      },
    ]),
    blacklist: Object.freeze([
      {
        ...documentedDropRule("drop-blacklisted-path", "any", "all"),
        pathKind: "blacklist",
      },
    ]),
    wildcards: Object.freeze([
      {
        ...defaultRule("login-scope"),
        type: "class:login",
        channel: "",
        incoming: "any",
        priority: 140,
      },
      {
        ...defaultRule("other-scope-bucket2"),
        type: "class:other",
        channel: "",
        incoming: "any",
        pathKind: "bucket:2",
        timing: "slow",
        priority: 130,
      },
    ]),
    factory: Object.freeze([
      {
        ...documentedDropRule("ota-outside-temp-radio", "ota", "all", 250),
        owner: "system",
        tempRadio: "inactive",
      },
      {
        ...documentedDropRule("wardriving-after-hop-4", "any", "5+", 220),
        owner: "system",
        channel: "#wardriving",
      },
    ]),
  });

  const COMPLETE_EXAMPLES = Object.freeze({
    ...EXAMPLES,
    system: EXAMPLES.factory,
    mixed: Object.freeze([
      ...clone(EXAMPLES.channel_scope),
      ...clone(EXAMPLES.prefix_rate),
      ...clone(EXAMPLES.moderation),
      ...clone(EXAMPLES.high_traffic),
      ...clone(EXAMPLES.factory),
    ]),
  });

  function initializeTool() {
    const root = document.querySelector("[data-filter-tool]");
    if (!root) return;
    const field = (name) => root.querySelector(`[data-field='${name}']`);
    const packetField = (name) => root.querySelector(`[data-packet='${name}']`);
    const liveDefinition = root.querySelector("[data-role='live-command']");
    const liveExplanation = root.querySelector("[data-role='live-explanation']");
    const liveWarnings = root.querySelector("[data-role='live-warnings']");
    const saveButton = root.querySelector("[data-role='save-rule']");
    const ruleList = root.querySelector("[data-role='rule-list']");
    const emptyPolicy = root.querySelector("[data-role='empty-policy']");
    const policySummary = root.querySelector("[data-role='policy-summary']");
    const policyWarningList = root.querySelector("[data-role='policy-warnings']");
    const targetProfile = root.querySelector("[data-role='target-profile']");
    const importInput = root.querySelector("[data-role='import-input']");
    const importError = root.querySelector("[data-role='import-error']");
    const explainResults = root.querySelector("[data-role='explain-results']");
    const simulationError = root.querySelector("[data-role='simulation-error']");
    const simulationResult = root.querySelector("[data-role='simulation-result']");
    const exportDsl = root.querySelector("[data-role='export-dsl']");
    const exportJson = root.querySelector("[data-role='export-json']");
    const exportBundle = root.querySelector("[data-role='export-bundle']");
    let rules = [];
    let editingId = "";
    let nextId = 1;

    function freshId() {
      let id;
      do { id = `rule-${nextId++}`; } while (rules.some((rule) => rule.id === id));
      return id;
    }

    function setValue(name, value) {
      const element = field(name);
      if (!element) return;
      if (element.type === "checkbox") element.checked = Boolean(value);
      else element.value = value == null ? "" : String(value);
    }

    function collectForm() {
      return normalizeRule({
        id: field("id").value,
        phase: field("phase").value,
        owner: field("owner").value,
        priority: field("priority").value,
        mode: field("mode").value,
        stop: field("stop").value,
        route: field("route").value,
        type: field("type").value,
        hops: field("hops").value,
        channel: field("channel").value,
        incoming: field("incoming").value,
        pathKind: field("path-kind").value,
        pathPrefix: field("path-prefix").value,
        sender: field("sender").value,
        tempRadio: field("temp-radio").value,
        verdict: field("verdict").value,
        scopeGate: field("scope-gate").value,
        targetKind: field("target-kind").value,
        target: field("target").value,
        rate: field("rate").value,
        burst: field("burst").value,
        timing: field("timing").value,
        queue: field("queue").value,
        retryBucket: field("retry-bucket").value,
        retryAttempts: field("retry-attempts").value,
        tag: field("tag").value,
      });
    }

    function writeForm(input, editing) {
      const rule = normalizeRule(input);
      editingId = editing ? rule.id : "";
      Object.entries({
        id: rule.id,
        phase: rule.phase,
        owner: rule.owner,
        priority: rule.priority,
        mode: rule.mode,
        stop: rule.stop,
        route: rule.route,
        type: rule.type,
        hops: rule.hops,
        channel: rule.channel,
        incoming: rule.incoming,
        "path-kind": rule.pathKind,
        "path-prefix": rule.pathPrefix,
        sender: rule.sender,
        "temp-radio": rule.tempRadio,
        verdict: rule.verdict,
        "scope-gate": rule.scopeGate,
        "target-kind": rule.targetKind,
        target: rule.target,
        rate: rule.rate,
        burst: rule.burst,
        timing: rule.timing,
        queue: rule.queue,
        "retry-bucket": rule.retryBucket,
        "retry-attempts": rule.retryAttempts,
        tag: rule.tag,
      }).forEach(([name, value]) => setValue(name, value));
      saveButton.textContent = editing ? "Update rule" : "Add rule to policy";
      updateFieldStates();
      updateLive();
    }

    function resetForm() {
      writeForm(defaultRule(freshId()), false);
    }

    function updateFieldStates() {
      field("path-prefix").disabled = field("path-kind").value !== "prefix";
      field("target").disabled = field("target-kind").value === "none";
      field("burst").disabled = clean(field("rate").value) === "";
      field("retry-attempts").disabled = field("retry-bucket").value === "none";
    }

    function suggestCompatibleExecution(element) {
      const name = element.getAttribute("data-field");
      if (name === "scope-gate" && field("scope-gate").value !== "unchanged") {
        setValue("phase", "scope_gate");
        setValue("owner", "scope");
        setValue("target-kind", "none");
        setValue("retry-bucket", "none");
        setValue("sender", "");
      } else if (name === "target-kind" && field("target-kind").value !== "none") {
        setValue("phase", "rewrite");
        setValue("scope-gate", "unchanged");
        setValue("retry-bucket", "none");
        setValue("sender", "");
        if (field("target-kind").value === "region" && field("owner").value === "filter") {
          setValue("owner", "scope");
        }
      } else if (name === "retry-bucket" && field("retry-bucket").value !== "none") {
        setValue("phase", "schedule");
        setValue("owner", "filter");
        setValue("scope-gate", "unchanged");
        setValue("target-kind", "none");
        setValue("sender", "");
      } else if (name === "sender" && clean(field("sender").value)) {
        setValue("phase", "content");
        setValue("owner", "filter");
        setValue("scope-gate", "unchanged");
        setValue("target-kind", "none");
        setValue("retry-bucket", "none");
      } else if ((name === "verdict" && field("verdict").value === "drop")
          || (name === "rate" && clean(field("rate").value))) {
        if (field("target-kind").value === "none") setValue("phase", "forward");
        if (field("owner").value === "scope") {
          setValue("owner", field("target-kind").value === "region" ? "admin" : "filter");
        }
      }
    }

    function renderWarnings(container, warnings) {
      container.replaceChildren();
      warnings.forEach((warning) => {
        const item = document.createElement("li");
        item.textContent = warning;
        container.appendChild(item);
      });
      container.hidden = warnings.length === 0;
    }

    function updateLive() {
      updateFieldStates();
      try {
        const rule = collectForm();
        liveDefinition.textContent = buildDefinition(rule);
        liveExplanation.textContent = explainRule(rule);
        renderWarnings(liveWarnings, ruleWarnings(rule));
        saveButton.disabled = false;
      } catch (error) {
        liveDefinition.textContent = "Complete a valid rule to generate its readable definition.";
        liveExplanation.textContent = error.message;
        renderWarnings(liveWarnings, []);
        saveButton.disabled = true;
      }
    }

    async function copyText(text, button) {
      try {
        await navigator.clipboard.writeText(text);
        const original = button.textContent;
        button.textContent = "Copied";
        global.setTimeout(() => { button.textContent = original; }, 1200);
      } catch (_error) {
        importError.textContent = "Browser clipboard access was denied. Select and copy the text manually.";
        importError.hidden = false;
      }
    }

    function makeButton(label, callback, className) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      if (className) button.className = className;
      button.addEventListener("click", callback);
      return button;
    }

    function renderPolicy() {
      const ordered = sortedRules(rules);
      ruleList.replaceChildren();
      emptyPolicy.hidden = ordered.length !== 0;
      ordered.forEach((rule, index) => {
        const item = document.createElement("li");
        const card = document.createElement("article");
        card.className = "filter-rule-card";
        const header = document.createElement("div");
        header.className = "filter-rule-card-header";
        const labels = document.createElement("div");
        labels.className = "filter-rule-labels";
        const name = document.createElement("strong");
        name.textContent = rule.id;
        const phase = document.createElement("span");
        phase.textContent = `Phase ${PHASE_ORDER[rule.phase] + 1}: ${PHASE_LABELS[rule.phase]}`;
        labels.append(name, phase);
        const meta = document.createElement("span");
        meta.className = "filter-rule-meta";
        meta.textContent = `order ${index + 1}, priority ${rule.priority}, ${rule.mode}, ~${estimateRuleBytes(rule)} bytes`;
        header.append(labels, meta);
        const definition = document.createElement("code");
        definition.className = "filter-rule-command";
        definition.textContent = buildDefinition(rule);
        const explanation = document.createElement("p");
        explanation.textContent = explainRule(rule);
        const warnings = document.createElement("ul");
        warnings.className = "filter-inline-warnings";
        renderWarnings(warnings, ruleWarnings(rule));
        const actions = document.createElement("div");
        actions.className = "filter-rule-actions";
        actions.append(
          makeButton("Edit", () => {
            writeForm(rule, true);
            root.querySelector(".filter-builder").scrollIntoView({ behavior: "smooth", block: "start" });
          }),
          makeButton("Copy", (event) => copyText(buildDefinition(rule), event.currentTarget)),
          makeButton("Remove", () => {
            rules = rules.filter((candidate) => candidate !== rule);
            if (editingId === rule.id) resetForm();
            renderPolicy();
          }, "filter-danger-action")
        );
        card.append(header, definition, explanation, warnings, actions);
        item.appendChild(card);
        ruleList.appendChild(item);
      });
      const estimate = rules.reduce((total, rule) => total + estimateRuleBytes(rule), 16);
      const budget = PROFILE_BUDGETS[targetProfile.value] || PROFILE_BUDGETS.nrf52;
      const percentage = Math.min(999, Math.round(estimate * 100 / budget));
      policySummary.textContent = `${rules.length} rule${rules.length === 1 ? "" : "s"} | approximate packed size ${estimate}/${budget} bytes (${percentage}%)`;
      renderWarnings(policyWarningList, policyWarnings(rules, targetProfile.value));
      exportDsl.value = ordered.map(buildDefinition).join("\n");
      exportJson.value = JSON.stringify(policyDocument(ordered), null, 2);
      exportBundle.value = encodeBundle(ordered);
    }

    function assignRules(inputRules) {
      rules = inputRules.map(normalizeRule);
      const numericIds = rules
        .map((rule) => rule.id.match(/^rule-(\d+)$/))
        .filter(Boolean)
        .map((match) => Number(match[1]));
      if (numericIds.length) nextId = Math.max(nextId, Math.max(...numericIds) + 1);
    }

    function renderExplanation(inputRules) {
      explainResults.replaceChildren();
      const heading = document.createElement("h3");
      heading.textContent = `${inputRules.length} parsed rule${inputRules.length === 1 ? "" : "s"}`;
      explainResults.appendChild(heading);
      const list = document.createElement("ol");
      sortedRules(inputRules).forEach((rule) => {
        const item = document.createElement("li");
        const definition = document.createElement("code");
        definition.textContent = buildDefinition(rule);
        const explanation = document.createElement("p");
        explanation.textContent = explainRule(rule);
        const warnings = document.createElement("ul");
        warnings.className = "filter-inline-warnings";
        renderWarnings(warnings, ruleWarnings(rule));
        item.append(definition, explanation, warnings);
        list.appendChild(item);
      });
      explainResults.appendChild(list);
      const warnings = document.createElement("ul");
      warnings.className = "filter-inline-warnings";
      renderWarnings(warnings, policyWarnings(inputRules, targetProfile.value));
      explainResults.appendChild(warnings);
      explainResults.hidden = false;
    }

    function parseImport() {
      importError.hidden = true;
      importError.textContent = "";
      try {
        return parsePolicyInput(importInput.value);
      } catch (error) {
        explainResults.hidden = true;
        importError.textContent = error.message;
        importError.hidden = false;
        return null;
      }
    }

    function collectPacket() {
      return normalizePacket({
        route: packetField("route").value,
        type: packetField("type").value,
        hops: packetField("hops").value,
        channel: packetField("channel").value,
        path: packetField("path").value,
        scopeStatus: packetField("scope-status").value,
        scopeName: packetField("scope-name").value,
        regionName: packetField("region-name").value,
        sender: packetField("sender").value,
        tempRadio: packetField("temp-radio").checked,
        blacklist: packetField("blacklist").value === "yes",
        buckets: packetField("buckets").value,
        loopLevel: packetField("loop-level").value,
      });
    }

    function decisionValue(label, value) {
      const item = document.createElement("div");
      const term = document.createElement("span");
      term.textContent = label;
      const content = document.createElement("strong");
      content.textContent = value;
      item.append(term, content);
      return item;
    }

    function renderSimulation(result) {
      simulationResult.replaceChildren();
      const heading = document.createElement("h3");
      heading.textContent = result.decision.drop
        ? "Final policy decision: DROP"
        : "Final policy decision: eligible, subject to remaining gates";
      const summary = document.createElement("div");
      summary.className = "filter-decision-grid";
      summary.append(
        decisionValue("Region gate", result.decision.scopeGate),
        decisionValue("Scope target", result.decision.scopeTarget ? `${result.decision.scopeTarget.kind}:${result.decision.scopeTarget.name}` : "unchanged"),
        decisionValue("Rate constraints", result.decision.rates.length ? result.decision.rates.map((rate) => `${rate.rate}/min burst ${rate.burst}`).join(", ") : "none"),
        decisionValue("Timing", result.decision.timing),
        decisionValue("Queue", result.decision.queue),
        decisionValue("Retry", result.decision.retry ? `${result.decision.retry.bucket}/${result.decision.retry.attempts}` : "none"),
        decisionValue("Tags", result.decision.tags.length ? result.decision.tags.join(", ") : "none")
      );
      const traceHeading = document.createElement("h3");
      traceHeading.textContent = "Evaluation trace";
      const trace = document.createElement("ol");
      trace.className = "filter-trace";
      result.trace.forEach((entry) => {
        const item = document.createElement("li");
        item.className = `filter-trace-${entry.status}`;
        const title = document.createElement("strong");
        title.textContent = `${entry.id} - ${entry.status}`;
        const detail = document.createElement("span");
        detail.textContent = entry.detail;
        item.append(title, detail);
        trace.appendChild(item);
      });
      simulationResult.append(heading, summary, traceHeading, trace);
      simulationResult.hidden = false;
    }

    root.querySelectorAll("[data-field]").forEach((element) => {
      element.addEventListener("input", () => {
        suggestCompatibleExecution(element);
        updateLive();
      });
      element.addEventListener("change", () => {
        suggestCompatibleExecution(element);
        updateLive();
      });
    });
    root.querySelector("[data-role='reset-form']").addEventListener("click", resetForm);
    saveButton.addEventListener("click", () => {
      try {
        const rule = collectForm();
        if (editingId) {
          const index = rules.findIndex((candidate) => candidate.id === editingId);
          if (index >= 0) rules[index] = rule;
          else rules.push(rule);
        } else rules.push(rule);
        resetForm();
        renderPolicy();
      } catch (_error) {
        updateLive();
      }
    });
    root.querySelector("[data-role='copy-live']").addEventListener("click", (event) => {
      if (liveDefinition.textContent) copyText(liveDefinition.textContent, event.currentTarget);
    });
    root.querySelector("[data-role='clear-policy']").addEventListener("click", () => {
      rules = [];
      resetForm();
      renderPolicy();
      simulationResult.hidden = true;
    });
    targetProfile.addEventListener("change", renderPolicy);
    root.querySelectorAll("[data-example]").forEach((button) => {
      button.addEventListener("click", () => {
        const example = COMPLETE_EXAMPLES[button.getAttribute("data-example")];
        if (!example) return;
        assignRules(clone(example));
        writeForm(rules[0], true);
        renderPolicy();
        simulationResult.hidden = true;
      });
    });
    root.querySelector("[data-role='explain-input']").addEventListener("click", () => {
      const parsed = parseImport();
      if (parsed) renderExplanation(parsed);
    });
    root.querySelector("[data-role='load-input']").addEventListener("click", () => {
      const parsed = parseImport();
      if (!parsed) return;
      assignRules(parsed);
      renderExplanation(rules);
      writeForm(rules[0], true);
      renderPolicy();
    });
    root.querySelector("[data-role='simulate']").addEventListener("click", () => {
      simulationError.hidden = true;
      try {
        renderSimulation(simulatePolicy(rules, collectPacket()));
      } catch (error) {
        simulationResult.hidden = true;
        simulationError.textContent = error.message;
        simulationError.hidden = false;
      }
    });
    root.querySelector("[data-role='reset-packet']").addEventListener("click", () => {
      packetField("route").value = "unscoped_flood";
      packetField("type").value = "grp_data";
      packetField("hops").value = "4";
      packetField("channel").value = "#rgdata";
      packetField("path").value = "860C,12A4";
      packetField("scope-status").value = "none";
      packetField("scope-name").value = "";
      packetField("region-name").value = "";
      packetField("sender").value = "";
      packetField("blacklist").value = "no";
      packetField("buckets").value = "";
      packetField("loop-level").value = "0";
      packetField("temp-radio").checked = false;
      simulationResult.hidden = true;
      simulationError.hidden = true;
    });
    root.querySelectorAll("[data-export-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        const selected = button.getAttribute("data-export-tab");
        root.querySelectorAll("[data-export-tab]").forEach((candidate) => {
          candidate.setAttribute("aria-selected", candidate === button ? "true" : "false");
        });
        root.querySelectorAll("[data-export-panel]").forEach((panel) => {
          panel.hidden = panel.getAttribute("data-export-panel") !== selected;
        });
      });
    });
    root.querySelector("[data-role='copy-export']").addEventListener("click", (event) => {
      const panel = Array.from(root.querySelectorAll("[data-export-panel]")).find((candidate) => !candidate.hidden);
      const textarea = panel ? panel.querySelector("textarea") : null;
      if (textarea) copyText(textarea.value, event.currentTarget);
    });
    root.querySelector("[data-role='download-json']").addEventListener("click", () => {
      const blob = new Blob([exportJson.value], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "meshcore-policy-engine-draft.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    });

    const requestedExample = new URLSearchParams(global.location.search).get("example");
    if (requestedExample && COMPLETE_EXAMPLES[requestedExample]) assignRules(clone(COMPLETE_EXAMPLES[requestedExample]));
    resetForm();
    renderPolicy();
  }

  const api = Object.freeze({
    BUNDLE_PREFIX,
    EXAMPLES: COMPLETE_EXAMPLES,
    FilterToolError,
    buildDefinition,
    decodeBundle,
    encodeBundle,
    estimateRuleBytes,
    explainRule,
    matchRule,
    normalizePacket,
    normalizeRule,
    parseDefinition,
    parsePolicyInput,
    policyDocument,
    policyWarnings,
    ruleWarnings,
    simulatePolicy,
    sortedRules,
  });

  global.MeshCoreFilterTool = api;
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initializeTool, { once: true });
    else initializeTool();
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
