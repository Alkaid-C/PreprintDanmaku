(() => {
  const DEFAULT_CONFIG = {
    title: "DanmakuHime Minimal Frontend",
    maxEvents: 200,
    fontSize: {
      title: "22px",
      meta: "14px",
      content: "16px",
      caption: "13px",
    },
    colors: {
      text: "#f5f7fb",
      muted: "#a9bad1",
      background: "rgba(8, 12, 20, 0.82)",
      card: "rgba(18, 26, 39, 0.86)",
      danmaku: "#2f855a",
      gift: "#b7791f",
      superchat: "#bf3f5c",
      guard: "#6b5bd6",
    },
  };

  const TYPES = new Set(["danmaku", "gift", "superchat", "guard"]);
  const TYPE_NAMES = {
    danmaku: "Danmaku",
    gift: "Gift",
    superchat: "SuperChat",
    guard: "Guard",
  };
  const GUARD_NAMES = {
    0: "无",
    1: "舰长",
    2: "提督",
    3: "总督",
  };

  const status = document.getElementById("status");
  const eventsNode = document.getElementById("events");
  const seen = new Set();
  const events = [];
  let maxEvents = DEFAULT_CONFIG.maxEvents;

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));

  const money = (value) => (
    typeof value === "number" && Number.isFinite(value)
      ? `${(value / 100).toFixed(2)} (${value}分)`
      : esc(value)
  );

  const guardName = (value) => GUARD_NAMES[value] || esc(value);

  function mergeConfig(config) {
    return {
      ...DEFAULT_CONFIG,
      ...config,
      colors: {
        ...DEFAULT_CONFIG.colors,
        ...(config.colors || {}),
      },
      fontSize: {
        ...DEFAULT_CONFIG.fontSize,
        ...(config.fontSize || {}),
      },
    };
  }

  async function loadConfig() {
    try {
      const response = await fetch("config.json", { cache: "no-store" });
      if (!response.ok) {
        return DEFAULT_CONFIG;
      }
      return mergeConfig(await response.json());
    } catch {
      return DEFAULT_CONFIG;
    }
  }

  function setVar(name, value) {
    if (value) {
      document.documentElement.style.setProperty(name, value);
    }
  }

  function applyConfig(config) {
    document.title = config.title;
    document.querySelector("h1").textContent = config.title;

    maxEvents = Number.isFinite(config.maxEvents)
      ? config.maxEvents
      : DEFAULT_CONFIG.maxEvents;

    setVar("--text", config.colors.text);
    setVar("--muted", config.colors.muted);
    setVar("--background", config.colors.background);
    setVar("--card", config.colors.card);
    setVar("--danmaku", config.colors.danmaku);
    setVar("--gift", config.colors.gift);
    setVar("--superchat", config.colors.superchat);
    setVar("--guard", config.colors.guard);
    setVar("--title-size", config.fontSize.title);
    setVar("--meta-size", config.fontSize.meta);
    setVar("--content-size", config.fontSize.content);
    setVar("--caption-size", config.fontSize.caption);
  }

  function setStatus(text, className = "") {
    status.textContent = text;
    status.className = `status ${className}`.trim();
  }

  function avatar(sender) {
    if (!sender.avatar_url) {
      return '<span class="avatar"></span>';
    }
    return `<img class="avatar" src="${esc(sender.avatar_url)}" alt="" referrerpolicy="no-referrer">`;
  }

  function identity(sender) {
    const badge = sender.badgename
      ? `${esc(sender.badgename)} lv. ${esc(sender.badgelevel)}`
      : "无牌";
    return `${esc(sender.username)} (${esc(sender.uid)}) - ${badge} - ${guardName(sender.guardstat)}`;
  }

  function meta(event) {
    const sender = event.sender || {};
    return `
      <div class="meta">
        <span class="token">[${esc(event.id)}]</span>
        <span class="type ${event.type}">[${TYPE_NAMES[event.type]}]</span>
        <span class="token">${esc(event.timestamp)}</span>
        ${avatar(sender)}
        <span class="sender">${identity(sender)}</span>
      </div>
    `;
  }

  function content(event) {
    if (event.type === "danmaku") {
      if (event.is_image && event.image_url) {
        return `
          <div class="content">
            <img class="image" src="${esc(event.image_url)}" alt="${esc(event.text)}" referrerpolicy="no-referrer">
            ${event.text ? `<div class="caption">${esc(event.text)}</div>` : ""}
          </div>
        `;
      }
      return `<div class="content">${esc(event.text)}</div>`;
    }

    if (event.type === "gift") {
      return `<div class="content gift">${esc(event.giftname)} x ${esc(event.giftcount)} - ${money(event.gifttotalvalue)}</div>`;
    }

    if (event.type === "superchat") {
      return `<div class="content superchat">SC lv. ${esc(event.level)} - ${money(event.value)} - ${esc(event.dwell_seconds)}s - ${esc(event.text)}</div>`;
    }

    return `<div class="content guard">${guardName(event.level)} - ${esc(event.months)}个月 - ${esc(event.dwell_seconds)}s</div>`;
  }

  function render() {
    eventsNode.className = "";
    eventsNode.innerHTML = events
      .slice()
      .sort((a, b) => Number(a.id) - Number(b.id))
      .map((event) => `<article class="event">${meta(event)}${content(event)}</article>`)
      .join("");
  }

  function handle(event) {
    if (!event || event.type === "init" || !TYPES.has(event.type)) {
      return;
    }

    const key = `${event.type}:${event.id}`;
    if (seen.has(key)) {
      return;
    }

    seen.add(key);
    events.push(event);
    events.sort((a, b) => Number(a.id) - Number(b.id));

    if (events.length > maxEvents) {
      const removed = events.splice(0, events.length - maxEvents);
      removed.forEach((item) => seen.delete(`${item.type}:${item.id}`));
    }

    render();
  }

  async function main() {
    applyConfig(await loadConfig());

    const stream = new EventSource("/stream");
    stream.onopen = () => setStatus("connected", "live");
    stream.onerror = () => setStatus("stream error", "error");
    stream.onmessage = (message) => {
      try {
        handle(JSON.parse(message.data));
      } catch (error) {
        setStatus("bad json", "error");
        console.error("Failed to parse SSE event", error);
      }
    };
  }

  main().catch((error) => {
    setStatus("no eventsource", "error");
    console.error("Failed to connect to /stream", error);
  });
})();
