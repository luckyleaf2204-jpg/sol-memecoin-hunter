import json, gzip, math, sys
sys.path.insert(0, '/home/user/sol-memecoin-hunter/smart-money-radar/backend')
from radar.tools import deep_copy as dc
d = json.load(gzip.open('/home/user/sol-memecoin-hunter/smart-money-radar/winner_data/paper/deep/deep_3kxcF8wHKm4sEtnxjXXeUvNGeDjTmo47LgtSRg71YfG5.json.gz', 'rt'))
W = d['windows']
allpos = dc.positions(d['trades'] + d['unpriceable'])
priced = [p for p in allpos if p['closed'] and all(t.get('state') for t in p['trades'])]

def reversed_ps(p):
    t = p['trades'][0]
    return t['venue'] == 'pumpswap' and t['state'].get('side') != t['side']

def covered(t, slot):
    w = W.get(t['sig'])
    if not w or not w['anchor']:
        return False
    if not w['truncated'] or w.get('decoded_to_slot', -1) >= slot:
        return True
    return any(s['slot'] > slot for s in w['states'])

out = {'reversed_pumpswap': [p['mint'] for p in priced if reversed_ps(p)],
       'pumpswap_total': sum(p['trades'][0]['venue'] == 'pumpswap' for p in priced)}
res = {}
for dl in dc.DELAYS:
    ds = math.ceil(dl / dc.SLOT_S)
    rows_all, rows_ver, cov = [], [], {'entry_exact': 0, 'entry_stale': 0, 'exit_exact': 0, 'exit_stale': 0}
    for p in priced:
        if reversed_ps(p):
            continue
        r = dc.simulate(p, W, dl)
        rows_all.append(r)
        ok = True
        if r.get('entry_price') is not None:
            e = covered(p['trades'][0], p['trades'][0]['slot'] + ds)
            cov['entry_exact' if e else 'entry_stale'] += 1
            ok &= e
        if r['copied']:
            for t in p['trades']:
                if t['side'] == 'sell':
                    e = covered(t, t['slot'] + ds)
                    cov['exit_exact' if e else 'exit_stale'] += 1
                    ok &= e
        if ok:
            rows_ver.append(r)
    res[dl] = {'coverage': cov, 'all_ex_ps': dc.stats(rows_all, len(rows_all)), 'verified_only': dc.stats(rows_ver, len(rows_ver))}
out['by_delay'] = res
# extra cost sensitivity: copier pays an extra Jito tip + priority of 0.001 SOL per transaction
sens = {}
for dl in dc.DELAYS:
    rs = [dc.simulate(p, W, dl) for p in priced if not reversed_ps(p)]
    rs2 = []
    for p, r in zip([p for p in priced if not reversed_ps(p)], rs):
        if r['copied']:
            n = 1 + sum(t['side'] == 'sell' for t in p['trades'])
            r = {**r, 'pnl': r['pnl'] - 0.001 * n}
            r['roi'] = r['pnl'] / dc.SIZE
        rs2.append(r)
    sens[dl] = dc.stats(rs2, len(rs2))
out['extra_tip_0p001'] = sens
json.dump(out, open('/home/user/sol-memecoin-hunter/smart-money-radar/winner_data/paper/deep/verify_3kxc.json', 'w'), indent=1, ensure_ascii=False)
print(json.dumps({'rev': len(out['reversed_pumpswap']), 'ps': out['pumpswap_total']}))
for dl in dc.DELAYS:
    a, v, s = res[dl]['all_ex_ps'], res[dl]['verified_only'], sens[dl]
    print(dl, res[dl]['coverage'], '| all', a['copied'], a['pnl_sol'], a['win_rate'], a['max_drawdown_sol'], a.get('pnl_ci95'),
          '| ver', v['signals'], v['copied'], v['pnl_sol'], v['roi_on_invested'], v['win_rate'], v['max_drawdown_sol'], v.get('pnl_ci95'), v['pnl_without_top5'],
          '| tip', s['pnl_sol'], s.get('pnl_ci95'))
