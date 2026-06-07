// danmaku-feed.jsx — live danmaku as an arXiv preprint body.
// MODEL
//   One combined FIFO queue holds danmaku + gifts (shared sequence, fixed cap).
//   Each new event enters its zone; the single oldest event retires from its
//   zone — one in, one out. Two zones:
//     · References  (danmaku)  — scrolling column, pinned bottom, clips at top.
//     · Acknowledgments (gifts) — visible list; retire = height collapse.
//   SuperChat + 上船(guard) leave the body into a TOP pinned zone with a
//   real time-based dwell (varies by amount/tier; max 3; oldest leaves first):
//     SuperChat→Remark/Observation · 舰长→Lemma · 提督→Theorem · 总督→Axiom.
// Exports to window: DanmakuFeed, useDanmakuStream, DM_THEMES.

/* ----------------------------------------------------------------- themes */
const DM_THEMES = {
  classic: {
    name: 'arXiv (white)',
    bg: '#fcfcfa', paper: '#ffffff', ink: '#16130f', inkSoft: '#6b6458',
    rule: '#ddd8cd', accent: '#16130f', boxBg: '#f4f1ea', boxRule: '#16130f',
    lineno: '#b8b1a3', vignette: 'none',
  },
};

const PREPRINT = 'VtuRXiv';
const RANK_MARK = ['', '†', '‡', '§'];                 // none / 舰长 / 提督 / 总督
const GUARD_NAME = ['', '舰长', '提督', '总督'];
const GUARD_ENV = ['', 'Lemma', 'Theorem', 'Axiom'];     // guard level 1/2/3
const SUPER_ENV = ['', 'Remark', 'Observation'];          // superchat level 1/2

// masthead meta — filled by the backend `init` event
const DM_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DM_TODAY = (() => { const d = new Date(); return d.getDate() + ' ' + DM_MONTHS[d.getMonth()] + ' ' + d.getFullYear(); })();
const DM_META = {
  date: DM_TODAY,
};

const CAP = 16;        // combined danmaku+gift live capacity (FIFO)
const PIN_MAX = 3;     // max simultaneously-pinned results

const pad = (n) => (n < 10 ? '0' + n : '' + n);
const fmt = (d) => pad(d.getHours()) + ':' + pad(d.getMinutes());

/* --------------------------------------------------------- stream emitter */
function useDanmakuStream() {
  const [body, setBody] = React.useState([]);      // live danmaku + gifts (FIFO)
  const [leaving, setLeaving] = React.useState([]); // gifts mid-collapse
  const [pinned, setPinned] = React.useState([]);   // pinned results (super/guard)
  const [leavingPins, setLeavingPins] = React.useState([]); // pins mid-collapse
  const [, setMetaVersion] = React.useState(0);
  const c = React.useRef({ ln: 0, env: 0, id: 0 });
  const expire = React.useRef({});
  const seen = React.useRef({});

  const applyInit = React.useCallback((ev) => {
    if (ev.stamp_label) DM_META.stampLabel = ev.stamp_label;
    if (Array.isArray(ev.authors)) DM_META.authors = ev.authors;
    if (ev.anchor) DM_META.anchor = ev.anchor;
    if (ev.room_title) DM_META.title = ev.room_title;
    if (ev.preprint_id) DM_META.preprintId = ev.preprint_id;
    if (ev.category) DM_META.category = ev.category;
    setMetaVersion((v) => v + 1);
  }, []);

  const preprintId = React.useCallback((id) => {
    const n = Math.abs(Number(id) || 0) % 9000;
    return PREPRINT + ':2606.' + String(1000 + n).padStart(4, '0');
  }, []);

  const senderOf = React.useCallback((sender) => {
    const s = sender || {};
    const rank = Math.max(0, Math.min(3, Number(s.guardstat) || 0));
    const badge = s.badgename || '';
    return {
      user: s.username || '匿名用户',
      rank,
      fan: badge ? { name: badge, level: Number(s.badgelevel) || 0 } : null,
    };
  }, []);

  const adapt = React.useCallback((ev) => {
    if (!ev || !ev.type) return null;
    if (ev.type === 'init') {
      applyInit(ev);
      return null;
    }

    if (ev.id != null) {
      const key = ev.type + ':' + ev.id;
      if (seen.current[key]) return null;
      seen.current[key] = true;
    }

    const state = c.current;
    const id = Number(ev.id) || ++state.id;
    state.id = Math.max(state.id, id);
    const time = ev.timestamp || fmt(new Date());
    const sender = senderOf(ev.sender);

    if (ev.type === 'danmaku') {
      return {
        id, type: 'normal', user: sender.user, time, rank: sender.rank,
        ln: ++state.ln,
        fan: sender.fan,
        preid: sender.fan ? null : preprintId(id),
        text: ev.text || '',
      };
    }

    if (ev.type === 'gift') {
      return {
        id, type: 'gift', user: sender.user, time, rank: sender.rank,
        gift: ev.giftname || '礼物',
        qty: Math.max(1, Number(ev.giftcount) || 1),
        value: Number(ev.gifttotalvalue) || 0,
      };
    }

    if (ev.type === 'superchat') {
      return {
        id, type: 'super', user: sender.user, time, rank: sender.rank,
        num: ++state.env,
        level: Number(ev.level) === 2 ? 2 : 1,
        text: ev.text || '',
        amount: Math.round((Number(ev.value) || 0) / 100),
        dwell: Math.max(1000, (Number(ev.dwell_seconds) || 12) * 1000),
      };
    }

    if (ev.type === 'guard') {
      const rank = Math.max(1, Math.min(3, Number(ev.level) || 1));
      const months = Math.max(1, Math.round(Number(ev.months) || 1));
      const verb = ev.newguard ? '开通了' : '续费了';
      return {
        id, type: 'guard', user: sender.user, time,
        num: ++state.env,
        rank,
        text: verb + months + '个月的' + GUARD_NAME[rank],
        dwell: Math.max(1000, (Number(ev.dwell_seconds) || 12) * 1000),
      };
    }

    return null;
  }, [applyInit, preprintId, senderOf]);

  const retirePin = React.useCallback((e) => {
    setLeavingPins((prev) => [...prev, e]);
    setTimeout(() => setLeavingPins((prev) => prev.filter((x) => x.id !== e.id)), 360);
  }, []);

  const pushPinned = React.useCallback((e) => {
    setPinned((prev) => {
      let next = [...prev, e];
      while (next.length > PIN_MAX) {
        const drop = next.shift();
        if (expire.current[drop.id]) { clearTimeout(expire.current[drop.id]); delete expire.current[drop.id]; }
        retirePin(drop);
      }
      return next;
    });
    expire.current[e.id] = setTimeout(() => {
      delete expire.current[e.id];
      setPinned((p) => p.filter((x) => x.id !== e.id));
      retirePin(e);
    }, e.dwell);
  }, [retirePin]);

  const emit = React.useCallback((e) => {
    if (e.type === 'super' || e.type === 'guard') { pushPinned(e); return; }
    setBody((prev) => {
      const next = [...prev, e];
      while (next.length > CAP) {
        const old = next.shift();
        if (old.type === 'gift') {
          setLeaving((lv) => [...lv, old]);
          setTimeout(() => setLeaving((lv) => lv.filter((x) => x.id !== old.id)), 380);
        }
      }
      return next;
    });
  }, [pushPinned]);

  React.useEffect(() => {
    let es = null;

    if (!window.EventSource || window.location.protocol === 'file:') {
      console.error('DanmakuHime requires the backend server and EventSource support.');
    } else {
      es = new EventSource('/stream');
      es.onmessage = (message) => {
        try {
          const ev = JSON.parse(message.data);
          const adapted = adapt(ev);
          if (adapted) emit(adapted);
        } catch (err) {
          console.error('SSE parse failed:', err, message.data);
        }
      };
      es.onerror = () => {
        console.error('SSE connection failed.');
      };
    }

    return () => {
      if (es) es.close();
      Object.values(expire.current).forEach(clearTimeout);
    };
  }, [adapt, emit]);

  const danmaku = body.filter((e) => e.type === 'normal');
  const gifts = body.filter((e) => e.type === 'gift');
  return { danmaku, gifts, leaving, pinned, leavingPins };
}

/* ----------------------------------------------------- collapse animation */
function Collapse({ leaving, animateIn, children }) {
  const ref = React.useRef(null);
  const [h, setH] = React.useState(animateIn ? 0 : 'auto');
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    if (leaving) {
      setH(el.scrollHeight);
      requestAnimationFrame(() => requestAnimationFrame(() => setH(0)));
    } else if (animateIn) {
      setH(el.scrollHeight);
      const t = setTimeout(() => setH('auto'), 360);
      return () => clearTimeout(t);
    }
  }, [leaving, animateIn]);
  return (
    <div className="dm-collapse" style={{ height: h === 'auto' ? 'auto' : h + 'px', opacity: leaving ? 0 : 1 }}>
      <div ref={ref}>{children}</div>
    </div>
  );
}

/* ----------------------------------------------------------- row renderers */
function DmCite({ e }) {
  return (
    <div className="dm-row dm-cite">
      <span className="dm-ln">{e.ln}</span>
      <span className="dm-content">
        <span className="dm-author">{e.user}</span>
        {e.rank ? <sup className="dm-guard">{RANK_MARK[e.rank]}</sup> : null}
        <span className="dm-sep">. </span>
        <span className="dm-quote">“{e.text}.”</span>
        <span className="dm-venue">
          {' '}
          {e.fan ? (
            <React.Fragment>
              <span className="dm-journal">{e.fan.name}</span>
              <span className="dm-tail">, Vol.&thinsp;{e.fan.level} ({e.time}).</span>
            </React.Fragment>
          ) : (
            <span className="dm-tail dm-preprint">{e.preid} ({e.time}).</span>
          )}
        </span>
      </span>
    </div>
  );
}

function DmFundLine({ e }) {
  return (
    <div className="dm-ack-line">
      <span className="dm-fund-tag">Funding.</span>{' '}
      <span className="dm-fund-body">
        {e.user}{e.rank ? <sup className="dm-guard">{RANK_MARK[e.rank]}</sup> : null} via <em>{e.gift}</em>
        <span className="dm-grant">&thinsp;×{e.qty}</span>.
      </span>
    </div>
  );
}

function DmBox({ e }) {
  const label = e.type === 'super' ? SUPER_ENV[e.level] : GUARD_ENV[e.rank];
  return (
    <div className="dm-thm">
      <div className="dm-pin-timer" style={{ animationDuration: (e.dwell || 12000) + 'ms' }} />
      <div className="dm-thm-head">
        <span className="dm-thm-label">{label} {e.num}</span>
        <span className="dm-thm-meta">
          {' ('}{e.user}{RANK_MARK[e.rank]}
          {e.amount ? <React.Fragment>, <span className="dm-thm-amt">¥{e.amount}</span></React.Fragment> : null}
          {').'}
        </span>
      </div>
      <div className="dm-thm-text">{e.text}.</div>
    </div>
  );
}

/* -------------------------------------------------------------- main feed */
function DanmakuFeed({ theme, stream, width, height }) {
  const t = DM_THEMES[theme] || DM_THEMES.classic;
  const { danmaku, gifts, leaving, pinned, leavingPins } = stream;
  const vars = {
    '--bg': t.bg, '--paper': t.paper, '--ink': t.ink, '--ink-soft': t.inkSoft,
    '--rule': t.rule, '--accent': t.accent, '--box-bg': t.boxBg, '--box-rule': t.boxRule,
    '--lineno': t.lineno,
    width: width || '100%', height: height || '100%',
  };
  const ackItems = [...leaving.map((e) => ({ e, leaving: true })), ...gifts.map((e) => ({ e, leaving: false }))];

  return (
    <div className="dm-root" style={vars}>
      <div className="dm-vignette" style={{ background: t.vignette }} />
      <div className="dm-stamp">
        {(DM_META.preprintId || DM_META.category) ? (
          <span className="dm-stamp-id">
            {DM_META.preprintId ? (DM_META.stampLabel ? DM_META.stampLabel + ':' : '') + DM_META.preprintId : null}
            {DM_META.category ? <React.Fragment>&nbsp;&nbsp;[{DM_META.category}]</React.Fragment> : null}
          </span>
        ) : <span />}
        <span>{DM_META.date}</span>
      </div>
      <header className="dm-head">
        {DM_META.title ? <h1 className="dm-title">{DM_META.title}</h1> : null}
        {DM_META.authors ? (
          <div className="dm-authors">
            {DM_META.authors.map((author, index) => (
              <div key={index}>
                {[author.name, author.affiliation].filter(Boolean).join('，')}
                {author.corresponding ? <span className="dm-corr"> ∗</span> : null}
              </div>
            ))}
          </div>
        ) : (
          DM_META.anchor ? <div className="dm-authors">{DM_META.anchor}</div> : null
        )}
      </header>

      {/* pinned results (superchat = Remark, 上船 = Lemma/Prop/Theorem) */}
      {(pinned.length + leavingPins.length) > 0 && (
        <div className="dm-pinned">
          {[
            ...[...pinned].reverse().map((e) => ({ e, lv: false })),
            ...leavingPins.map((e) => ({ e, lv: true })),
          ].map(({ e, lv }) => (
            <Collapse key={e.id} animateIn={!e.seed} leaving={lv}>
              <div className="dm-pin-slot"><DmBox e={e} /></div>
            </Collapse>
          ))}
        </div>
      )}

      <div className="dm-secrule"><span>References</span></div>

      {/* References — scrolling danmaku column */}
      <div className="dm-feed">
        <div className="dm-feed-mask" />
        <div className="dm-stack">
          {danmaku.map((e) => (
            <div className="dm-slot" key={e.id}><DmCite e={e} /></div>
          ))}
        </div>
      </div>

      {/* Acknowledgments — gifts */}
      {ackItems.length > 0 && (
        <div className="dm-ack">
          <div className="dm-ack-head">Acknowledgments</div>
          {ackItems.map(({ e, leaving: lv }) => (
            <Collapse key={e.id} animateIn={!e.seed} leaving={lv}>
              <DmFundLine e={e} />
            </Collapse>
          ))}
        </div>
      )}

    </div>
  );
}

Object.assign(window, { DanmakuFeed, useDanmakuStream, DM_THEMES });
