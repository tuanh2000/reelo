"use client";

// ===== Screen 2: New Series Wizard — chat + outline (ported from screen-wizard.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, ChatBubble } from "@/components/ui";
import { MiniMark } from "@/components/logo";
import { DEMO_FALLBACK, type Nav, type OutlineItem, type SeriesDraft } from "@/lib/data";
import { DEMO_WIZARD_OUTLINE } from "@/lib/demo-fixtures";
import {
  sendWizardMessage,
  getProviderKeys,
  ApiError,
  type ProviderKeyItem,
} from "@/lib/api";

interface Msg {
  role: "ai" | "user";
  text: string;
}

const WIZ_GREETING: Msg = {
  role: "ai",
  text:
    'Chào bạn 👋 Mình là trợ lý sản xuất của Reelo. Bạn muốn làm series về chủ đề gì? Cứ nói chung chung cũng được — ví dụ "tôn giáo", "lịch sử La Mã", "vũ trụ"…',
};

// Demo (offline) seed conversation — only used when NEXT_PUBLIC_REQUIRE_AUTH=false.
const WIZ_SEED_MSGS: Msg[] = [
  WIZ_GREETING,
  { role: "user", text: "Mình muốn làm series giới thiệu các tôn giáo lớn trên thế giới, kiểu học thuật nhưng dễ xem." },
  {
    role: "ai",
    text:
      "Tuyệt vời! Mình đề xuất một series 5 tập, mỗi tập một tôn giáo lớn, đi từ nguồn gốc → giáo lý cốt lõi → ảnh hưởng. Bạn xem dàn ý bên phải nhé — cứ chat để mình chỉnh sửa, thêm/bớt tập, hay đổi giọng văn.",
  },
];

const WIZ_SUGGEST = [
  "Thêm 1 tập về vô thần & thuyết bất khả tri",
  "Rút còn 4 tập",
  "Cho giọng văn học thuật hơn",
  "Thêm hook mở đầu mỗi tập",
];

function WizardOutlineItem({
  ep,
  idx,
  total,
  onToggle,
  onDelete,
  onMove,
  onEdit,
}: {
  ep: OutlineItem;
  idx: number;
  total: number;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onMove: (idx: number, dir: number) => void;
  onEdit: (id: string, t: string) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [val, setVal] = React.useState(ep.title);
  React.useEffect(() => setVal(ep.title), [ep.title]);
  return (
    <div
      className="card"
      style={{ padding: 13, display: "flex", gap: 11, alignItems: "flex-start", boxShadow: "none", opacity: ep.pick ? 1 : 0.55, transition: ".2s" }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2, color: "var(--text-3)", paddingTop: 2 }}>
        <button className="icon-btn" style={{ width: 22, height: 18 }} disabled={idx === 0} onClick={() => onMove(idx, -1)}>
          <Icon name="chevron-up" size={15} />
        </button>
        <button className="icon-btn" style={{ width: 22, height: 18 }} disabled={idx === total - 1} onClick={() => onMove(idx, 1)}>
          <Icon name="chevron-down" size={15} />
        </button>
      </div>
      <button
        onClick={() => onToggle(ep.id)}
        style={{
          marginTop: 3,
          width: 20,
          height: 20,
          flex: "none",
          borderRadius: 6,
          border: `2px solid ${ep.pick ? "var(--brand)" : "var(--border-strong)"}`,
          background: ep.pick ? "var(--brand)" : "transparent",
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {ep.pick && <Icon name="check" size={13} strokeWidth={3} />}
      </button>
      <div style={{ flex: 1, minWidth: 0 }}>
        {editing ? (
          <input
            className="field"
            autoFocus
            value={val}
            style={{ padding: "5px 9px", fontSize: 14, fontWeight: 700 }}
            onChange={(e) => setVal(e.target.value)}
            onBlur={() => {
              onEdit(ep.id, val);
              setEditing(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                onEdit(ep.id, val);
                setEditing(false);
              }
            }}
          />
        ) : (
          <div style={{ fontWeight: 700, fontSize: 14.5, lineHeight: 1.35 }}>{ep.title}</div>
        )}
        <div className="muted" style={{ fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
          {ep.desc}
        </div>
      </div>
      <div style={{ display: "flex", gap: 2 }}>
        <button className="icon-btn" style={{ width: 30, height: 30 }} onClick={() => setEditing(true)}>
          <Icon name="pencil" size={15} />
        </button>
        <button className="icon-btn" style={{ width: 30, height: 30 }} onClick={() => onDelete(ep.id)}>
          <Icon name="trash-2" size={15} />
        </button>
      </div>
    </div>
  );
}

// No account-level gate anymore: the wizard chat just needs ONE usable script
// provider (keyless, or one the user has a per-user key for). The full per-series
// toolset (image / voice) is chosen later on the Setup screen. If the user has no
// usable script provider at all, we point them at the key page.
function NoScriptGate({ nav }: { nav: Nav }) {
  return (
    <div className="page" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "70vh" }}>
      <Card style={{ padding: 28, maxWidth: 460, textAlign: "center" }}>
        <span
          style={{
            width: 56,
            height: 56,
            borderRadius: 16,
            background: "var(--brand-tint)",
            color: "var(--brand)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            marginBottom: 16,
          }}
        >
          <Icon name="key-round" size={26} />
        </span>
        <h2 style={{ fontSize: 20, marginBottom: 10 }}>Cần key cho provider kịch bản</h2>
        <p className="muted" style={{ fontSize: 14, lineHeight: 1.6, marginBottom: 20 }}>
          Trợ lý cần ít nhất một provider viết kịch bản dùng được. Hãy thêm key cho một provider
          (vd Gemini, Claude…) trong trang Cấu hình AI — key dùng chung cho mọi series.
        </p>
        <Button variant="primary" size="md" icon="arrow-right" onClick={() => nav({ name: "settings" })}>
          Đi tới Cấu hình AI
        </Button>
      </Card>
    </div>
  );
}

// A usable script provider = keyless, or one the user has a per-user key for.
function usableScriptProviders(items: ProviderKeyItem[]): ProviderKeyItem[] {
  return items.filter((p) => !p.requires_key || p.has_key);
}

export function WizardScreen({ nav }: { nav: Nav }) {
  // Script-provider readiness: gather the providers the user can chat with. The
  // chosen one is passed to sendWizardMessage (per-series script provider) and
  // carried forward into the draft so Setup pre-selects it. undefined = checking.
  const [scriptOptions, setScriptOptions] = React.useState<ProviderKeyItem[] | undefined>(
    DEMO_FALLBACK ? [] : undefined,
  );
  const [scriptProvider, setScriptProvider] = React.useState<string>("");

  React.useEffect(() => {
    if (DEMO_FALLBACK) return;
    getProviderKeys()
      .then((k) => {
        const usable = usableScriptProviders(k.script);
        setScriptOptions(usable);
        setScriptProvider((cur) => cur || usable[0]?.id || "");
      })
      .catch(() => setScriptOptions([])); // backend unreachable → empty (gate offers key page)
  }, []);

  // Demo mode (offline) keeps the canned conversation + seed outline so the UI is
  // usable with no backend. Prod starts from a single greeting and lets the real
  // wizard LLM (POST /wizard/message) drive both the reply and the outline.
  const [msgs, setMsgs] = React.useState<Msg[]>(DEMO_FALLBACK ? WIZ_SEED_MSGS : [WIZ_GREETING]);
  const [outline, setOutline] = React.useState<OutlineItem[]>(DEMO_FALLBACK ? DEMO_WIZARD_OUTLINE : []);
  const [name, setName] = React.useState("Các tôn giáo lớn của thế giới");
  const [input, setInput] = React.useState("");
  const [typing, setTyping] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, typing]);

  const send = (textArg?: string) => {
    const text = (textArg ?? input).trim();
    if (!text || typing) return;
    // Snapshot history BEFORE adding the new user turn (backend takes prior turns
    // as context + `idea` as the latest message).
    const history = msgs.map((m) => ({ role: m.role, text: m.text }));
    setMsgs((m) => [...m, { role: "user", text }]);
    setInput("");
    setTyping(true);
    setError(null);

    // Pass the chosen per-series script provider so Phase A uses it.
    sendWizardMessage(text, history, scriptProvider ? { provider: scriptProvider } : {})
      .then(({ reply, outline: nextOutline }) => {
        setMsgs((m) => [...m, { role: "ai", text: reply }]);
        // Backend returns the full refined outline when it changed; keep the
        // user's manual edits otherwise (outline omitted).
        if (nextOutline) setOutline(nextOutline);
      })
      .catch((e) => {
        const msg =
          e instanceof ApiError
            ? e.message
            : "Không kết nối được trợ lý. Vui lòng thử lại.";
        setError(msg);
        setMsgs((m) => [
          ...m,
          { role: "ai", text: `⚠️ ${msg}` },
        ]);
      })
      .finally(() => setTyping(false));
  };

  // Build the draft handed to the Setup screen (and ultimately approveSeries).
  // Carry the chosen script provider so Setup pre-selects the per-series toolset.
  const buildDraft = (): SeriesDraft => ({
    name: name.trim() || "Series chưa đặt tên",
    topic: name.trim(),
    outline,
    ...(scriptProvider
      ? { providers: { script: scriptProvider, image: "", voice: "" } }
      : {}),
  });

  const picked = outline.filter((o) => o.pick).length;
  const toggle = (id: string) => setOutline((o) => o.map((e) => (e.id === id ? { ...e, pick: !e.pick } : e)));
  const del = (id: string) => setOutline((o) => o.filter((e) => e.id !== id));
  const edit = (id: string, t: string) => setOutline((o) => o.map((e) => (e.id === id ? { ...e, title: t } : e)));
  const move = (idx: number, dir: number) =>
    setOutline((o) => {
      const n = [...o];
      const j = idx + dir;
      if (j < 0 || j >= n.length) return o;
      [n[idx], n[j]] = [n[j], n[idx]];
      return n;
    });

  // Gate: still checking → spinner; no usable script provider → route to key page.
  if (scriptOptions === undefined) {
    return (
      <div className="page" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "70vh" }}>
        <Icon name="loader" size={26} style={{ color: "var(--brand)" }} />
      </div>
    );
  }
  if (scriptOptions.length === 0 && !DEMO_FALLBACK) {
    return <NoScriptGate nav={nav} />;
  }

  return (
    <div className="page page-wide" style={{ height: "100%", display: "flex", flexDirection: "column", paddingBottom: 24 }}>
      <div style={{ marginBottom: 18 }}>
        <h2 style={{ fontSize: 22 }}>Trợ lý tạo series</h2>
        <p className="muted" style={{ fontSize: 14, marginTop: 4 }}>
          Trò chuyện để cùng AI phác thảo dàn ý các tập. Chốt lại khi bạn ưng ý.
        </p>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* LEFT: chat */}
        <Card style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div className="chat-ava">
              <MiniMark size={18} />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14.5 }}>Trợ lý Reelo</div>
              <div className="subtle" style={{ fontSize: 12 }}>
                Chọn AI viết kịch bản · phản hồi tức thì
              </div>
            </div>
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
              {scriptOptions && scriptOptions.length > 0 && (
                <select
                  className="field"
                  value={scriptProvider}
                  disabled={typing}
                  onChange={(e) => setScriptProvider(e.target.value)}
                  title="Provider viết kịch bản (cho series này)"
                  style={{ fontSize: 12.5, padding: "5px 8px", maxWidth: 200 }}
                >
                  {scriptOptions.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              )}
              <Badge tone="green" icon="circle-dot">
                Online
              </Badge>
            </div>
          </div>

          <div ref={scrollRef} className="scroll-y" style={{ flex: 1, padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>
            {msgs.map((m, i) => (
              <ChatBubble key={i} role={m.role}>
                {m.text}
              </ChatBubble>
            ))}
            {typing && (
              <ChatBubble role="ai">
                <span className="mono" style={{ letterSpacing: 2 }}>
                  ● ● ●
                </span>
              </ChatBubble>
            )}
          </div>

          <div style={{ padding: "0 18px 10px", display: "flex", gap: 8, flexWrap: "wrap" }}>
            {WIZ_SUGGEST.map((s) => (
              <button key={s} className="btn btn-secondary btn-sm" style={{ fontSize: 12.5 }} onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>
          {error && (
            <div
              className="subtle"
              style={{ padding: "0 18px 8px", fontSize: 12.5, color: "var(--danger, #ef3e36)", display: "flex", alignItems: "center", gap: 6 }}
            >
              <Icon name="alert-triangle" size={13} /> {error}
            </div>
          )}
          <div style={{ padding: 14, borderTop: "1px solid var(--border)", display: "flex", gap: 10 }}>
            <input
              className="field"
              placeholder="Nhập tin nhắn cho trợ lý…"
              value={input}
              disabled={typing}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") send();
              }}
            />
            <Button variant="primary" icon="send" className="btn-icon" disabled={typing} onClick={() => send()} />
          </div>
        </Card>

        {/* RIGHT: outline */}
        <Card style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                <Icon name="list-checks" size={18} style={{ color: "var(--brand)" }} />
                <h3 style={{ fontSize: 16 }}>Dàn ý series</h3>
              </div>
              <Badge tone="brand">{picked}/{outline.length} tập sẽ sản xuất</Badge>
            </div>
            <input
              className="field"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Tên series…"
              style={{ marginTop: 12, fontWeight: 700 }}
            />
          </div>

          <div className="scroll-y" style={{ flex: 1, padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            {outline.map((ep, i) => (
              <WizardOutlineItem
                key={ep.id}
                ep={ep}
                idx={i}
                total={outline.length}
                onToggle={toggle}
                onDelete={del}
                onMove={move}
                onEdit={edit}
              />
            ))}
            <button
              className="btn btn-ghost btn-md"
              style={{ border: "1.5px dashed var(--border-strong)", color: "var(--text-2)" }}
              onClick={() =>
                setOutline((o) => [
                  ...o,
                  { id: "w" + Date.now(), title: `Tập ${o.length + 1} — Tập mới`, desc: "Nhấn bút chì để đặt tiêu đề.", pick: true },
                ])
              }
            >
              <Icon name="plus" size={17} /> Thêm tập thủ công
            </button>
          </div>

          <div style={{ padding: 14, borderTop: "1px solid var(--border)", display: "flex", gap: 10, alignItems: "center" }}>
            <div className="muted" style={{ fontSize: 13, flex: 1 }}>
              Ước tính ~{picked * 4} phút nội dung
            </div>
            <Button
              variant="primary"
              size="md"
              icon="arrow-right"
              disabled={picked === 0}
              onClick={() =>
                nav({
                  name: "setup",
                  draft: buildDraft(),
                  toast: "Dàn ý đã sẵn sàng · cấu hình provider tiếp theo",
                })
              }
            >
              Tiếp tục: cấu hình
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
