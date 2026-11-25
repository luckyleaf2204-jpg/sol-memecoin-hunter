"""Build the 3kxc deep copy-trade report (md + docx + xlsx) from the replay result. No network access."""
import datetime as dt
import json
import os
import statistics as st

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "..", "docs", "research", "buoc")
W = "3kxcF8wHKm4sEtnxjXXeUvNGeDjTmo47LgtSRg71YfG5"
R = json.load(open(os.path.join(HERE, f"deep_{W}_result.json")))
V = json.load(open(os.path.join(HERE, "verify_3kxc.json")))
LIVE = [json.loads(x) for x in open(os.path.join(HERE, "..", "live_detections.jsonl"))]
TZ = dt.timezone(dt.timedelta(hours=7))
D = ["1", "3", "5", "8", "10"]
P = sorted(R["positions_detail"], key=lambda p: p["open_bt"])


def vn(x, n=2):
    if x is None:
        return "–"
    s = f"{x:,.{n}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x, n=1):
    return "–" if x is None else vn(x * 100, n) + "%"


def day(t):
    return dt.datetime.fromtimestamp(t, TZ).strftime("%d/%m/%Y")


def ts(t):
    return dt.datetime.fromtimestamp(t, TZ).strftime("%d/%m/%Y %H:%M:%S")


B, L = R["by_delay"], sorted(x["detect_delay_s"] for x in LIVE)
live_med, live_p90, live_max = st.median(L), L[int(0.9 * len(L)) - 1], L[-1]
live_3k = [x["detect_delay_s"] for x in LIVE if x["wallet"] == W]
wo = R["wallet_original"]


def weekly(k):
    wk = {}
    for p in P:
        w = dt.datetime.fromtimestamp(p["open_bt"], TZ).strftime("%G-W%V")
        g = wk.setdefault(w, {"pos": 0, "copied": 0, "pnl": 0.0, "wins": 0, "wallet": 0.0, "first": p["open_bt"]})
        g["pos"] += 1
        g["wallet"] += p["wallet_pnl"]
        if p[k].get("copied"):
            g["copied"] += 1
            g["pnl"] += p[k]["pnl"]
            g["wins"] += p[k]["pnl"] > 0
    return wk


WK5, WK8 = weekly("d5"), weekly("d8")
cost3 = {d: sum(p[f"d{d}"]["pnl"] - 0.003 * (1 + p["sells"]) for p in P if p[f"d{d}"].get("copied")) for d in D}
tip1 = V["extra_tip_0p001"]

# ------------------------------------------------------------------ content blocks
blocks = []
H = lambda t, lv=1: blocks.append(("h", lv, t))
T = lambda t: blocks.append(("p", t))
BL = lambda items: blocks.append(("ul", items))
TB = lambda head, rows: blocks.append(("t", head, rows))

H("BÁO CÁO KIỂM CHỨNG COPY TRADE VÍ 3kxcF8wH… (REPLAY SÂU, ĐỘ TRỄ 1/3/5/8/10 GIÂY)", 0)
T(f"Ngày lập: {dt.datetime.now(TZ).strftime('%d/%m/%Y')}. Ví: {W}. "
  f"Khoảng dữ liệu: {day(R['window']['first'])} – {day(R['window']['last'])} ({R['window']['signatures']} chữ ký giao dịch gần nhất của ví). "
  f"Nguồn: RPC công khai Solana (api.mainnet-beta.solana.com), không dùng Helius credit, không mua API. "
  "Tổng số lệnh gọi RPC: 21.073 (lượt đọc chính 14.248 + đọc bổ sung cửa sổ giá 6.825); lượt tính lại cuối dùng checkpoint, không gọi mạng.")

H("I. KẾT LUẬN (TRẢ LỜI 5 CÂU HỎI)")
b5, b8, b10 = B["5"], B["8"], B["10"]
TB(["Câu hỏi", "Trả lời"], [
    ["1. Copy trade ví này có lãi không?",
     f"CÓ, trong mẫu {day(R['window']['first'])}–{day(R['window']['last'])}: lãi ròng ở cả 5 mức trễ sau mọi chi phí; khoảng tin cậy 95% đều > 0. "
     f"Nhưng lãi đến gần như hoàn toàn từ pool Meteora DAMM v2; pool Meteora DBC hòa/lỗ từ 5 giây trở lên. Lãi giảm mạnh theo tuần (xem mục V)."],
    ["2. Độ trễ nào có lãi ổn định?",
     f"≤ 5 giây: ổn định (5 s: +{vn(b5['pnl_sol'])} SOL, CI95 {vn(b5['pnl_ci95'][0])}–{vn(b5['pnl_ci95'][1])}, bỏ 5 lệnh lãi nhất vẫn +{vn(b5['pnl_without_top5'])}). "
     f"8 giây: còn lãi (+{vn(b8['pnl_sol'])}, bỏ top 5 còn +{vn(b8['pnl_without_top5'])}). 10 giây: lãi mỏng (bỏ top 5 còn +{vn(b10['pnl_without_top5'])}). "
     f"Độ trễ phát hiện thật đo trên RPC công khai: trung vị {vn(live_med)} s, p90 {vn(live_p90)} s, chưa cộng thời gian gửi lệnh → mức thực tế cần dùng là 8–10 giây, không phải 1–3 giây."],
    ["3. PnL ròng sau toàn bộ chi phí?",
     f"Mức 8 giây (sát thực tế): +{vn(b8['pnl_sol'])} SOL trên {b8['copied']} lệnh × 0,5 SOL (ROI {pct(b8['roi_on_invested'])}/lệnh). "
     f"Mức 10 giây: +{vn(b10['pnl_sol'])} SOL. Nếu người copy phải trả thêm 0,003 SOL phí ưu tiên/Jito mỗi giao dịch: 8 s còn +{vn(cost3['8'])}, 10 s còn +{vn(cost3['10'])} SOL."],
    ["4. Drawdown tối đa và rủi ro lớn nhất?",
     f"Drawdown tối đa {vn(b8['max_drawdown_sol'])} SOL ở 8 s ({vn(b10['max_drawdown_sol'])} ở 10 s) ≈ 7–8 lệnh 0,5 SOL mất trắng liên tiếp. "
     f"Rủi ro lớn nhất: lợi thế đang suy giảm – ROI copy theo tuần (8 s) {pct(WK8['2026-W45']['pnl']/(WK8['2026-W45']['copied']*.5))} → {pct(WK8['2026-W46']['pnl']/(WK8['2026-W46']['copied']*.5))} → {pct(WK8['2026-W47']['pnl']/(WK8['2026-W47']['copied']*.5))}; "
     f"số vị thế/tuần của ví giảm 140 → 117 → 39 → 6. Thứ hai: phụ thuộc đuôi phải (trung vị ROI/lệnh âm, win rate < 50%)."],
    ["5. Đủ điều kiện theo dõi Smart Money dài hạn?",
     "ĐỦ ĐIỀU KIỆN THEO DÕI (watchlist + paper trade trực tiếp), CHƯA ĐỦ ĐIỀU KIỆN COPY TIỀN THẬT. Điều kiện nâng hạng: paper trade trực tiếp 2–4 tuần với độ trễ phát hiện thật, "
     "PnL 2 tuần trượt ở 8 s > 0 và ví duy trì ≥ 30 vị thế/tuần. Dừng theo dõi nếu 2 tuần liên tiếp PnL copy ≤ 0."],
])

H("II. PHẠM VI DỮ LIỆU VÀ PHƯƠNG PHÁP")
c = R["window"]["counts"]
BL([f"Đọc {R['window']['signatures']} chữ ký gần nhất của ví: {c['swap']} swap do ví tự ký; {c['not_signed']} giao dịch không do ví ký (spam/airdrop/chuyển vào); {c['failed']} giao dịch lỗi; {c['signed_other']} giao dịch tự ký khác; {c['multi_token']} giao dịch nhiều token (bỏ qua).",
    f"Vị thế đã đóng: {R['positions']['closed']}; định giá được: {R['positions']['priced']} (đạt yêu cầu ≥ 100); không định giá được: {R['positions']['not_priceable']} (8 vị thế PumpSwap pool đảo chiều – token nằm ở phía quote, mô hình dự trữ không áp dụng; ví gốc lãi {vn(sum(x['wallet_pnl'] for x in R['unpriceable_positions']))} SOL trên 8 vị thế này, KHÔNG tính vào kết quả copy). Vị thế còn mở cuối kỳ: {R['positions']['open_at_end']} (không tính).",
    "Giữ nguyên cả lệnh lãi, lệnh lỗ và lệnh không copy được (không lọc kết quả đẹp).",
    "Lệnh copy: 0,5 SOL/lệnh mua đầu tiên của vị thế; bán theo đúng tỷ lệ ví gốc bán ở từng lệnh bán, mỗi lệnh bán cũng trễ đúng bằng độ trễ mô phỏng.",
    "Giá khớp: tính lại trên TRẠNG THÁI POOL THẬT tại slot (thời điểm lệnh ví + độ trễ; 1 slot = 0,4 s). Meteora DAMM v2/DBC: giải mã sự kiện EvtSwap2 của mọi giao dịch trong pool, mô hình thanh khoản tập trung (L, √giá), phí theo pool. Không dùng giá giao dịch kế tiếp làm giá copy.",
    f"Kiểm chứng mô hình: chuỗi trạng thái pool Meteora dự đoán khớp trạng thái thật {R['model_check']['meteora_match']:,}/{R['model_check']['meteora_pairs']:,} cặp ({pct(R['model_check']['meteora_match']/R['model_check']['meteora_pairs'],2)}).".replace(",", "."),
    "Cửa sổ giá: mỗi lệnh của ví đọc toàn bộ giao dịch của pool trong 25 slot (10 s) sau đó. 405 cửa sổ có > 30 giao dịch được đọc bổ sung (4.699 giao dịch) để mức 8/10 s cũng dùng trạng thái thật; 1 cửa sổ đọc bổ sung thất bại (ảnh hưởng 1 lệnh bán ở 8 s và 10 s – ghi nhận là ước tính).",
    "Chi phí người copy: phí mạng + phí ưu tiên + Jito tip đúng bằng ví gốc đã trả (trung vị 0,000028 SOL/vị thế – ví không trả Jito tip); phí sàn (phí pool) nằm trong mô hình khớp; trượt giá = tác động giá thật của 0,5 SOL lên pool. Kiểm tra độ nhạy thêm 0,001 và 0,003 SOL/giao dịch.",
    "Không khớp lệnh khi: giá đã tăng > 25% so với giá ví mua (ngưỡng đuổi giá); tác động giá của 0,5 SOL > 10% (thanh khoản mỏng); ví gốc đã bán trước khi lệnh copy vào. Độ nhạy ngưỡng đuổi giá 10% ở mục VI.",
])

H("III. KẾT QUẢ THEO ĐỘ TRỄ (302 TÍN HIỆU, 0,5 SOL/LỆNH, SAU MỌI CHI PHÍ)")
TB(["Chỉ tiêu"] + [f"{d} giây" for d in D], [
    ["Lệnh copy được"] + [f"{B[d]['copied']}/{B[d]['signals']}" for d in D],
    ["Tỷ lệ thành công (khớp được)"] + [pct(B[d]["success_rate"]) for d in D],
    ["Lệnh khớp dùng trạng thái pool thật"] + [f"{B[d]['exact_state_fills']}/{B[d]['copied']}" for d in D],
    ["PnL ròng (SOL)"] + [vn(B[d]["pnl_sol"]) for d in D],
    ["Vốn quay vòng (SOL)"] + [vn(B[d]["invested_sol"], 1) for d in D],
    ["ROI bình quân/lệnh"] + [pct(B[d]["roi_on_invested"]) for d in D],
    ["ROI trung vị/lệnh"] + [pct(B[d]["median_roi"]) for d in D],
    ["Win rate"] + [pct(B[d]["win_rate"]) for d in D],
    ["Drawdown tối đa (SOL)"] + [vn(B[d]["max_drawdown_sol"]) for d in D],
    ["Lệnh lỗ nặng nhất (SOL)"] + [vn(B[d]["worst_trade_sol"]) for d in D],
    ["Khoảng tin cậy 95% PnL (SOL)"] + [f"{vn(B[d]['pnl_ci95'][0])} – {vn(B[d]['pnl_ci95'][1])}" for d in D],
    ["Xác suất PnL ≤ 0 (bootstrap)"] + [pct(B[d]["prob_pnl_le_0"]) for d in D],
    ["Giá copy cao hơn giá ví (trung vị)"] + [pct(B[d]["median_chase"]) for d in D],
    ["Tác động giá 0,5 SOL (trung vị)"] + [pct(B[d]["median_impact"]) for d in D],
    ["Không khớp: giá tăng quá 25%"] + [str(B[d]["not_copied"].get("giá tăng quá ngưỡng", 0)) for d in D],
    ["Không khớp: ví gốc bán trước"] + [str(B[d]["not_copied"].get("ví gốc bán trước khi lệnh copy vào", 0)) for d in D],
    ["Không khớp: thanh khoản mỏng (>10%)"] + [str(B[d]["not_copied"].get("thanh khoản mỏng", 0)) for d in D],
])
T(f"Đối chiếu ví gốc cùng {wo['positions']} vị thế: PnL on-chain +{vn(wo['pnl_sol'])} SOL trên {vn(wo['sol_in'],1)} SOL vốn vào, win rate {pct(wo['win_rate'])}, "
  f"thời gian giữ trung vị {int(wo['median_hold_s'])} giây. Người copy chỉ giữ lại khoảng 5–8% lợi nhuận/vốn của ví vì vào sau và phải chịu tác động giá của chính lệnh mình.")

H("IV. PHÂN TÍCH THEO SÀN")
rows = []
for v, d in R["by_venue"].items():
    name = {"damm": "Meteora DAMM v2", "dbc": "Meteora DBC (bonding curve)", "pumpswap": "PumpSwap"}.get(v, v)
    rows.append([name, str(d["positions"]), vn(d["wallet_pnl"])] + [f"{vn(d[k]['pnl_sol'])} ({d[k]['copied']})" for k in D])
rows.append(["PumpSwap (pool đảo chiều)", "8", vn(sum(x["wallet_pnl"] for x in R["unpriceable_positions"]))] + ["không định giá"] * 5)
TB(["Sàn", "Vị thế", "PnL ví gốc (SOL)"] + [f"Copy {d} s: PnL (lệnh)" for d in D], rows)
dbc = R["by_venue"]["dbc"]
T(f"Toàn bộ lợi nhuận copy đến từ DAMM v2. DBC: chỉ dương ở 1–3 giây, từ 5 giây trở lên âm (CI95 ở 8 s: {vn(dbc['8']['pnl_ci95'][0])} – {vn(dbc['8']['pnl_ci95'][1])}). "
  "Không có vị thế pump.fun curve hay PumpSwap thường trong mẫu; nhóm 'sàn khác' bằng 0.")

H("V. MỨC ĐỘ PHỤ THUỘC VÀO MỘT VÀI LỆNH LÃI LỚN VÀ ĐỘ ỔN ĐỊNH THEO THỜI GIAN")
TB(["Chỉ tiêu"] + [f"{d} giây" for d in D], [
    ["PnL ròng"] + [vn(B[d]["pnl_sol"]) for d in D],
    ["Bỏ 1 lệnh lãi nhất"] + [vn(B[d]["pnl_without_top1"]) for d in D],
    ["Bỏ 3 lệnh lãi nhất"] + [vn(B[d]["pnl_without_top3"]) for d in D],
    ["Bỏ 5 lệnh lãi nhất"] + [vn(B[d]["pnl_without_top5"]) for d in D],
    ["Tỷ trọng 3 lệnh lãi nhất"] + [pct(B[d]["top3_share"]) for d in D],
])
T("Kết quả vẫn dương khi bỏ 5 lệnh lãi lớn nhất ở mọi độ trễ, nhưng ở 8–10 giây 5 lệnh này chiếm khoảng 60–63% lợi nhuận.")
rows = []
for w in sorted(WK8):
    a, b = WK5[w], WK8[w]
    rows.append([w, f"từ {day(a['first'])}", str(a["pos"]), vn(a["wallet"], 1), vn(a["pnl"]), pct(a["pnl"] / (a["copied"] * .5)) if a["copied"] else "–",
                 vn(b["pnl"]), pct(b["pnl"] / (b["copied"] * .5)) if b["copied"] else "–"])
TB(["Tuần", "Bắt đầu", "Vị thế ví", "PnL ví gốc", "Copy 5 s: PnL", "Copy 5 s: ROI/lệnh", "Copy 8 s: PnL", "Copy 8 s: ROI/lệnh"], rows)
T("Tuần 45 tạo ra phần lớn lợi nhuận; tuần 46 ROI chỉ còn khoảng 2–3%/lệnh; hoạt động của ví giảm mạnh từ tuần 47 (tuần 48 mới có 1 ngày dữ liệu, 6 vị thế – không đủ để kết luận).")

H("VI. ĐỘ NHẠY CHI PHÍ VÀ NGƯỠNG ĐUỔI GIÁ")
C10 = R["sensitivity_chase_10pct"]
TB(["Kịch bản"] + [f"{d} giây" for d in D], [
    ["Cơ sở (phí bằng ví gốc)"] + [vn(B[d]["pnl_sol"]) for d in D],
    ["+0,001 SOL phí ưu tiên/Jito mỗi giao dịch"] + [vn(tip1[d]["pnl_sol"]) for d in D],
    ["+0,003 SOL phí ưu tiên/Jito mỗi giao dịch"] + [vn(cost3[d]) for d in D],
    ["Ngưỡng đuổi giá 10% (thay 25%): PnL"] + [vn(C10[d]["pnl_sol"]) for d in D],
    ["Ngưỡng 10%: số lệnh khớp"] + [str(C10[d]["copied"]) for d in D],
    ["Ngưỡng 10%: drawdown tối đa"] + [vn(C10[d]["max_drawdown_sol"]) for d in D],
])
T("Ngưỡng đuổi giá 10% bỏ khoảng 30–40% số lệnh, giảm PnL ít hơn và giảm drawdown còn khoảng −1,5 đến −1,9 SOL: đây là cấu hình an toàn hơn nếu sau này copy thật.")

H("VII. ĐỐI CHIẾU ĐỘ TRỄ PHÁT HIỆN THẬT VỚI ĐỘ TRỄ MÔ PHỎNG")
TB(["Nguồn đo", "Số lần", "Trung vị (s)", "p90 (s)", "Tối đa (s)"], [
    ["Theo dõi trực tiếp 5 ví qua RPC công khai (polling)", str(len(L)), vn(live_med), vn(live_p90), vn(live_max)],
    ["Riêng ví 3kxc", str(len(live_3k)), vn(st.median(live_3k)), "–", vn(max(live_3k))],
])
T("Độ trễ mô phỏng là thời gian từ lệnh của ví đến lúc lệnh copy KHỚP. Thực tế = độ trễ phát hiện (trung vị ~5 s, p90 ~8 s) + thời gian ký, gửi và vào khối (thêm ~0,5–2 s). "
  "Vì vậy với hạ tầng miễn phí hiện tại, kịch bản sát thực tế là 8 giây (trung vị) đến 10 giây (p90); mức 1–3 giây chỉ đạt được với stream trả phí (đo trước đây: Helius WebSocket trung vị 2,18 s, p90 3,11 s).")

H("VIII. HẠN CHẾ CỦA KIỂM CHỨNG")
BL(["Mẫu 21 ngày, 1 ví, 302 vị thế: đủ cho thống kê mô tả, chưa đủ để khẳng định lợi thế bền vững (lợi nhuận tập trung ở tuần đầu).",
    "Không mô phỏng MEV/sandwich nhắm vào lệnh copy và tỷ lệ giao dịch thất bại của người copy (giao dịch rơi, hết hạn blockhash).",
    "Lệnh bán của người copy được tính trên pool thật KHÔNG có lệnh mua của chính người copy (thận trọng: thực tế giá bán có thể cao hơn một chút).",
    "8 vị thế PumpSwap pool đảo chiều và 44 vị thế còn mở không được tính; 1 lệnh bán ở 8/10 s dùng trạng thái ước tính.",
    "Chi phí người copy lấy bằng chi phí ví gốc (rất thấp); trong thực tế cạnh tranh khối cần phí ưu tiên cao hơn – xem độ nhạy mục VI."])

H("IX. KIẾN NGHỊ")
BL(["Giữ ví 3kxc trong danh sách theo dõi Smart Money ở trạng thái 'theo dõi – paper trade', không bật copy tiền thật (trading_enabled vẫn = false).",
    "Chạy paper trade trực tiếp (độ trễ phát hiện thật) 2–4 tuần; chỉ xem xét nâng hạng khi PnL 2 tuần trượt ở 8 s > 0, ví duy trì ≥ 30 vị thế/tuần và ROI/lệnh không tiếp tục giảm.",
    "Nếu sau này copy: chỉ copy pool Meteora DAMM v2, bỏ DBC; ngưỡng đuổi giá 10%; giới hạn tác động giá 5%.",
    "Đánh giá lại hàng tuần bằng tool replay (dữ liệu checkpoint đã lưu, chỉ đọc phần mới)."])

# ------------------------------------------------------------------ markdown
md = []
for b in blocks:
    if b[0] == "h":
        md.append(("# " if b[1] == 0 else "## ") + b[2] + "\n")
    elif b[0] == "p":
        md.append(b[1] + "\n")
    elif b[0] == "ul":
        md += ["- " + x for x in b[1]] + [""]
    else:
        md.append("| " + " | ".join(b[1]) + " |")
        md.append("|" + "---|" * len(b[1]))
        md += ["| " + " | ".join(r) + " |" for r in b[2]] + [""]
open(os.path.join(OUT, "bao-cao-copy-sau-3kxc.md"), "w", encoding="utf-8").write("\n".join(md))

# ------------------------------------------------------------------ docx (Times New Roman)
doc = Document()
for s in doc.sections:
    s.left_margin, s.right_margin, s.top_margin, s.bottom_margin = Cm(3), Cm(2), Cm(2), Cm(2)
    s.orientation = 1
    s.page_width, s.page_height = s.page_height, s.page_width
stl = doc.styles["Normal"]
stl.font.name = "Times New Roman"
stl.font.size = Pt(13)
stl.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")


def run_font(r, size=13, bold=False, color=None):
    r.font.name = "Times New Roman"
    r._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    r.font.size = Pt(size)
    r.bold = bold
    if color:
        r.font.color.rgb = RGBColor(*color)


hd = doc.add_paragraph()
hd.alignment = WD_ALIGN_PARAGRAPH.CENTER
run_font(hd.add_run("SMART MONEY RADAR – BỘ PHẬN NGHIÊN CỨU"), 12, True)
for b in blocks:
    if b[0] == "h":
        p = doc.add_paragraph()
        if b[1] == 0:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_font(p.add_run(b[2]), 14 if b[1] == 0 else 13, True, (0, 0x66, 0x5E))
    elif b[0] == "p":
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        run_font(p.add_run(b[1]))
    elif b[0] == "ul":
        for x in b[1]:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.left_indent = Cm(0.6)
            run_font(p.add_run("- " + x))
    else:
        t = doc.add_table(rows=1, cols=len(b[1]))
        t.style = "Table Grid"
        for i, h in enumerate(b[1]):
            c = t.rows[0].cells[i]
            c.text = ""
            run_font(c.paragraphs[0].add_run(h), 11, True)
        for r in b[2]:
            cells = t.add_row().cells
            for i, v in enumerate(r):
                cells[i].text = ""
                run_font(cells[i].paragraphs[0].add_run(v), 11, i == 0 and len(b[1]) == 2)
        doc.add_paragraph()
doc.save(os.path.join(OUT, "bao-cao-copy-sau-3kxc.docx"))

# ------------------------------------------------------------------ xlsx with formulas
wb = Workbook()
thin = Side(style="thin", color="999999")
BD = Border(left=thin, right=thin, top=thin, bottom=thin)
HF = PatternFill("solid", fgColor="006A5E")


def style_head(ws, row=1):
    for c in ws[row]:
        c.font = Font(name="Times New Roman", bold=True, color="FFFFFF")
        c.fill = HF
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BD


def finish(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name="Times New Roman")
            c.border = BD
    ws.freeze_panes = "B2"


ws = wb.active
ws.title = "Vi the"
head = ["STT", "Token", "Sàn", "Mở lệnh", "Tuần", "Giữ (giây)", "Ví: số lệnh mua", "Ví: số lệnh bán", "Ví: vốn vào (SOL)", "Ví: PnL on-chain (SOL)",
        "Chữ ký mua đầu", "Chữ ký bán cuối"]
for d in D:
    head += [f"{d}s: copy được", f"{d}s: giá cao hơn ví", f"{d}s: tác động giá", f"{d}s: chi phí (SOL)", f"{d}s: PnL (SOL)",
             f"{d}s: trạng thái pool", f"{d}s: lý do không copy"]
ws.append(head)
vname = {"damm": "Meteora DAMM v2", "dbc": "Meteora DBC", "pumpswap": "PumpSwap"}
for i, p in enumerate(P, 1):
    row = [i, p["mint"], vname.get(p["venue"], p["venue"]), ts(p["open_bt"]), dt.datetime.fromtimestamp(p["open_bt"], TZ).strftime("%G-W%V"),
           p["hold_s"], p["buys"], p["sells"], p["wallet_in"], p["wallet_pnl"], p["open_sig"], p["close_sig"]]
    for d in D:
        s = p[f"d{d}"]
        row += ["Có" if s.get("copied") else "Không", s.get("chase"), s.get("impact"), s.get("cost"), s.get("pnl"),
                ("thật" if s.get("exact_state") else "ước tính") if s.get("copied") else "", s.get("why", "")]
    ws.append(row)
style_head(ws)
finish(ws, [6, 46, 18, 20, 10, 10, 9, 9, 12, 12, 30, 30] + [10, 11, 11, 11, 11, 11, 30] * 5)
N = len(P) + 1


def col(d, k):   # column letter of field k (0..6) for delay d
    return get_column_letter(13 + D.index(d) * 7 + k)


s1 = wb.create_sheet("Tong hop", 0)
s1.append(["Chỉ tiêu (công thức trên sheet Vi the)"] + [f"Trễ {d} giây" for d in D])
lines = [
    ("Số tín hiệu (vị thế đóng định giá được)", lambda d: f"=COUNTA('Vi the'!B2:B{N})"),
    ("Lệnh copy được", lambda d: f"=COUNTIF('Vi the'!{col(d,0)}2:{col(d,0)}{N},\"Có\")"),
    ("Tỷ lệ thành công", lambda d: f"={get_column_letter(2 + D.index(d))}3/{get_column_letter(2 + D.index(d))}2"),
    ("PnL ròng sau chi phí (SOL)", lambda d: f"=SUM('Vi the'!{col(d,4)}2:{col(d,4)}{N})"),
    ("Vốn quay vòng (SOL)", lambda d: f"={get_column_letter(2 + D.index(d))}3*0.5"),
    ("ROI bình quân/lệnh", lambda d: f"={get_column_letter(2 + D.index(d))}5/{get_column_letter(2 + D.index(d))}6"),
    ("Win rate", lambda d: f"=COUNTIFS('Vi the'!{col(d,0)}2:{col(d,0)}{N},\"Có\",'Vi the'!{col(d,4)}2:{col(d,4)}{N},\">0\")/{get_column_letter(2 + D.index(d))}3"),
    ("Tổng chi phí mạng + ưu tiên + Jito (SOL)", lambda d: f"=SUM('Vi the'!{col(d,3)}2:{col(d,3)}{N})"),
    ("Lệnh khớp dùng trạng thái pool thật", lambda d: f"=COUNTIF('Vi the'!{col(d,5)}2:{col(d,5)}{N},\"thật\")"),
    ("Không khớp: giá tăng quá ngưỡng 25%", lambda d: f"=COUNTIF('Vi the'!{col(d,6)}2:{col(d,6)}{N},\"giá đã tăng*\")"),
    ("Không khớp: ví gốc bán trước", lambda d: f"=COUNTIF('Vi the'!{col(d,6)}2:{col(d,6)}{N},\"ví gốc bán*\")"),
    ("Không khớp: thanh khoản mỏng", lambda d: f"=COUNTIF('Vi the'!{col(d,6)}2:{col(d,6)}{N},\"thanh khoản*\")"),
    ("PnL DAMM v2 (SOL)", lambda d: f"=SUMIFS('Vi the'!{col(d,4)}2:{col(d,4)}{N},'Vi the'!C2:C{N},\"Meteora DAMM v2\")"),
    ("PnL DBC (SOL)", lambda d: f"=SUMIFS('Vi the'!{col(d,4)}2:{col(d,4)}{N},'Vi the'!C2:C{N},\"Meteora DBC\")"),
    ("PnL nếu +0,003 SOL/giao dịch", lambda d: f"={get_column_letter(2 + D.index(d))}5-SUMPRODUCT(('Vi the'!{col(d,0)}2:{col(d,0)}{N}=\"Có\")*(1+'Vi the'!H2:H{N}))*0.003"),
    ("Drawdown tối đa (SOL) – từ tool", lambda d: B[d]["max_drawdown_sol"]),
    ("CI95 PnL thấp – từ tool (bootstrap)", lambda d: B[d]["pnl_ci95"][0]),
    ("CI95 PnL cao – từ tool (bootstrap)", lambda d: B[d]["pnl_ci95"][1]),
    ("PnL bỏ 5 lệnh lãi nhất – từ tool", lambda d: B[d]["pnl_without_top5"]),
]
for name, f in lines:
    s1.append([name] + [f(d) for d in D])
s1.append([])
s1.append(["Ví gốc: PnL on-chain (SOL)", f"=SUM('Vi the'!J2:J{N})"])
s1.append(["Ví gốc: vốn vào (SOL)", f"=SUM('Vi the'!I2:I{N})"])
s1.append(["Ví gốc: win rate", f"=COUNTIF('Vi the'!J2:J{N},\">0\")/COUNTA('Vi the'!B2:B{N})"])
style_head(s1)
finish(s1, [44, 14, 14, 14, 14, 14])
for r in s1.iter_rows(min_row=2):
    for c in r[1:]:
        c.number_format = "0.0%" if r[0].value in ("Tỷ lệ thành công", "ROI bình quân/lệnh", "Win rate", "Ví gốc: win rate") else "#,##0.000"

s2 = wb.create_sheet("Theo tuan", 1)
s2.append(["Tuần", "Vị thế ví", "PnL ví gốc (SOL)"] + [f"Copy {d}s: PnL (SOL)" for d in D] + [f"Copy {d}s: lệnh" for d in D])
for w in sorted(WK8):
    r = len(s2["A"]) + 1
    s2.append([w, f"=COUNTIF('Vi the'!E2:E{N},A{r})", f"=SUMIFS('Vi the'!J2:J{N},'Vi the'!E2:E{N},A{r})"]
              + [f"=SUMIFS('Vi the'!{col(d,4)}2:{col(d,4)}{N},'Vi the'!E2:E{N},A{r})" for d in D]
              + [f"=COUNTIFS('Vi the'!E2:E{N},A{r},'Vi the'!{col(d,0)}2:{col(d,0)}{N},\"Có\")" for d in D])
style_head(s2)
finish(s2, [12, 10, 14] + [13] * 10)

s3 = wb.create_sheet("Do tre that")
s3.append(["Ví", "Chữ ký", "Slot", "Thời điểm khối", "Thời điểm phát hiện", "Độ trễ phát hiện (giây)"])
for x in LIVE:
    s3.append([x["wallet"], x["sig"], x["slot"], ts(x["block_time"]), ts(x["detected_at"]), x["detect_delay_s"]])
n3 = len(LIVE) + 1
s3.append([])
s3.append(["Trung vị", "", "", "", "", f"=MEDIAN(F2:F{n3})"])
s3.append(["p90", "", "", "", "", f"=PERCENTILE(F2:F{n3},0.9)"])
s3.append(["Tối đa", "", "", "", "", f"=MAX(F2:F{n3})"])
style_head(s3)
finish(s3, [46, 30, 12, 20, 20, 14])

s4 = wb.create_sheet("Khong dinh gia")
s4.append(["Token", "Sàn", "Chữ ký mua đầu", "Lý do", "PnL ví gốc (SOL)"])
for x in R["unpriceable_positions"]:
    s4.append([x["mint"], x["venue"], x["open_sig"], x["reason"], x["wallet_pnl"]])
style_head(s4)
finish(s4, [46, 12, 30, 40, 14])

s5 = wb.create_sheet("Gia dinh")
for a in [("Ví", W), ("Khoảng dữ liệu", f"{day(R['window']['first'])} – {day(R['window']['last'])}"), ("Vốn/lệnh", "0,5 SOL"),
          ("Độ trễ mô phỏng", "1/3/5/8/10 giây (1 slot = 0,4 giây)"), ("Ngưỡng đuổi giá", "25% (độ nhạy 10%)"), ("Ngưỡng tác động giá", "10%"),
          ("Chi phí người copy", "phí mạng + ưu tiên + Jito tip bằng ví gốc; phí pool trong mô hình khớp"),
          ("Giá khớp", "trạng thái pool thật tại slot ví + độ trễ (giải mã EvtSwap2 Meteora)"),
          ("Kiểm chứng mô hình", f"{R['model_check']['meteora_match']}/{R['model_check']['meteora_pairs']} cặp trạng thái khớp"),
          ("Nguồn", "RPC công khai Solana, không dùng Helius credit")]:
    s5.append(list(a))
finish(s5, [26, 90])
wb.save(os.path.join(OUT, "bao-cao-copy-sau-3kxc.xlsx"))
print("ok")
