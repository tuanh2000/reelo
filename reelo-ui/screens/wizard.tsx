"use client";

// ===== Screen 2: New Series Wizard — chat + outline (ported from screen-wizard.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, ChatBubble } from "@/components/ui";
import { MiniMark } from "@/components/logo";
import { SERIES, WIZARD_SEED, type Nav, type OutlineItem } from "@/lib/data";

interface Msg {
  role: "ai" | "user";
  text: string;
}

const WIZ_SEED_MSGS: Msg[] = [
  {
    role: "ai",
    text:
      'Chào bạn 👋 Mình là trợ lý sản xuất của Reelo. Bạn muốn làm series về chủ đề gì? Cứ nói chung chung cũng được — ví dụ "tôn giáo", "lịch sử La Mã", "vũ trụ"…',
  },
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

export function WizardScreen({ nav }: { nav: Nav }) {
  const [msgs, setMsgs] = React.useState<Msg[]>(WIZ_SEED_MSGS);
  const [outline, setOutline] = React.useState<OutlineItem[]>(WIZARD_SEED);
  const [input, setInput] = React.useState("");
  const [typing, setTyping] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, typing]);

  const send = (textArg?: string) => {
    const text = (textArg ?? input).trim();
    if (!text) return;
    setMsgs((m) => [...m, { role: "user", text }]);
    setInput("");
    setTyping(true);
    // TODO(backend): replace this canned simulation with api.sendWizardMessage()
    // to drive the chat + outline from the real LLM.
    setTimeout(() => {
      setTyping(false);
      let reply = "Đã cập nhật dàn ý bên phải cho bạn. Bạn muốn chỉnh thêm gì nữa không?";
      const low = text.toLowerCase();
      if (low.includes("thêm") || low.includes("vô thần")) {
        reply = "Mình đã thêm một tập mới vào cuối dàn ý. Bạn xem thử nhé!";
        setOutline((o) => [
          ...o,
          {
            id: "w" + Date.now(),
            title: `Tập ${o.length + 1} — Vô thần & thuyết bất khả tri`,
            desc: "Góc nhìn phi tôn giáo, lịch sử tư tưởng và tranh luận hiện đại.",
            pick: true,
          },
        ]);
      } else if (low.includes("rút") || low.includes("4 tập")) {
        reply = "Đã rút gọn xuống 4 tập trọng tâm nhất, bỏ tập có độ trùng lặp cao.";
        setOutline((o) => o.slice(0, 4));
      } else if (low.includes("học thuật") || low.includes("giọng")) {
        reply = "Đã ghi nhận: giọng văn sẽ trang trọng, trích dẫn nguồn sử liệu rõ ràng hơn. Áp dụng cho toàn series.";
      } else if (low.includes("hook")) {
        reply = "Tuyệt — mỗi tập sẽ mở đầu bằng một câu hỏi gây tò mò trong 10 giây đầu để giữ chân người xem.";
      }
      setMsgs((m) => [...m, { role: "ai", text: reply }]);
    }, 900);
  };

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
          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10 }}>
            <div className="chat-ava">
              <MiniMark size={18} />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14.5 }}>Trợ lý Reelo</div>
              <div className="subtle" style={{ fontSize: 12 }}>
                Dùng Claude · phản hồi tức thì
              </div>
            </div>
            <div style={{ marginLeft: "auto" }}>
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
          <div style={{ padding: 14, borderTop: "1px solid var(--border)", display: "flex", gap: 10 }}>
            <input
              className="field"
              placeholder="Nhập tin nhắn cho trợ lý…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") send();
              }}
            />
            <Button variant="primary" icon="send" className="btn-icon" onClick={() => send()} />
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
            <input className="field" defaultValue="Các tôn giáo lớn của thế giới" style={{ marginTop: 12, fontWeight: 700 }} />
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
            <Button variant="secondary" size="md" icon="palette" onClick={() => nav({ name: "style", series: SERIES[0] })}>
              Chọn style
            </Button>
            <Button
              variant="primary"
              size="md"
              icon="check"
              onClick={() => nav({ name: "setup", series: SERIES[0], toast: "Đã lưu series · cấu hình provider tiếp theo" })}
            >
              Chốt &amp; Lưu
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
