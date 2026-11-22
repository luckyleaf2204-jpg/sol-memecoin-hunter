export function statusLabel(s: string) {
  return ({ smart: "Smart Money", tracked: "Đang theo dõi", candidate: "Ứng viên", observed: "Không đạt / bỏ qua",
            excluded: "Đã loại trừ" } as Record<string, string>)[s] || s;
}

export function flowLabel(f: string) {
  return ({ ACCUMULATING: "Đang gom", DISTRIBUTING: "Đang phân phối", BALANCED: "Dòng tiền cân bằng" } as Record<string, string>)[f] || f;
}

export const RELATION_LABEL: Record<string, string> = {
  funded_by: "Nạp SOL trực tiếp", direct_sol_transfer: "Chuyển SOL qua lại", direct_token_transfer: "Chuyển token qua lại",
  common_funder: "Cùng nguồn funding", same_creator: "Liên quan cùng creator", sync_buys: "Mua đồng bộ bất thường",
};
