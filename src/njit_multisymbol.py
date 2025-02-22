from __future__ import annotations

import os

import numpy as np

if "NOJIT" in os.environ and os.environ["NOJIT"] == "true":
    print("not using numba")

    def njit(pyfunc=None, **kwargs):
        def wrap(func):
            return func

        if pyfunc is not None:
            return wrap(pyfunc)
        else:
            return wrap

    def prange(start, stop=None, step=1):
        if stop is None:
            return range(start)
        return range(start, stop, step)

else:
    print("using numba")
    from numba import njit, prange as nb_prange

    prange = nb_prange


from njit_funcs import (
    calc_ema,
    calc_new_psize_pprice,
    qty_to_cost,
    cost_to_qty,
    calc_close_grid_long,
    calc_close_grid_short,
    calc_pnl_long,
    calc_pnl_short,
    round_,
    round_up,
    round_dn,
    calc_min_entry_qty,
    calc_bankruptcy_price,
    calc_pprice_diff_int,
)
from njit_funcs_recursive_grid import calc_recursive_entry_long, calc_recursive_entry_short


@njit
def calc_pnl_sum(poss_long, poss_short, lows, highs, c_mults):
    pnl_sum = 0.0
    for i in range(len(poss_long)):
        pnl_sum += calc_pnl_long(poss_long[i][1], lows[i], poss_long[i][0], False, c_mults[i])
    for i in range(len(poss_short)):
        pnl_sum += calc_pnl_short(poss_short[i][1], highs[i], poss_short[i][0], False, c_mults[i])
    return pnl_sum


@njit
def get_open_orders_long(
    close_price,
    balance,
    pos_long,
    emas,
    unstucking_close,
    inverse,
    qty_step,
    price_step,
    min_qty,
    min_cost,
    c_mult,
    cfgl,
):
    # returns entries and closes
    entries = calc_recursive_entry_long(
        balance,
        pos_long[0],
        pos_long[1],
        close_price,
        min(emas),
        inverse,
        qty_step,
        price_step,
        min_qty,
        min_cost,
        c_mult,
        cfgl[10],
        cfgl[9],
        cfgl[5],
        cfgl[14],
        cfgl[15],
        cfgl[16],
        cfgl[1],
        cfgl[3],
        cfgl[0] or cfgl[2],
    )
    closes = calc_close_grid_long(
        cfgl[4],  # backwards_tp
        balance,
        max(0.0, abs(pos_long[0]) - abs(unstucking_close[0])) if unstucking_close[0] else pos_long[0],
        pos_long[1],
        close_price,  # close price
        max(emas),
        0,  # utc_now_ms: timed AU is disabled
        0,  # prev_AU_fill_ts_close: timed AU is disabled
        inverse,
        qty_step,
        price_step,
        min_qty,
        min_cost,
        c_mult,
        cfgl[16],  # wallet_exposure_limit
        cfgl[12],  # min_markup,
        cfgl[11],  # markup_range,
        cfgl[13],  # n_close_orders,
        cfgl[3],  # auto_unstuck_wallet_exposure_threshold,
        cfgl[1],  # auto_unstuck_ema_dist,
        cfgl[0],  # auto_unstuck_delay_minutes,
        cfgl[2],  # auto_unstuck_qty_pct,
    )
    if unstucking_close[0]:
        # closes = [unstucking_close] + closes
        closes = sorted([unstucking_close] + closes, key=lambda x: x[1])
    return entries, closes


@njit
def get_open_orders_short(
    close_price,
    balance,
    pos_short,
    emas,
    unstucking_close,
    inverse,
    qty_step,
    price_step,
    min_qty,
    min_cost,
    c_mult,
    cfgs,
):
    # returns entries and closes
    entries = calc_recursive_entry_short(
        balance,
        pos_short[0],
        pos_short[1],
        close_price,
        max(emas),
        inverse,
        qty_step,
        price_step,
        min_qty,
        min_cost,
        c_mult,
        cfgs[10],
        cfgs[9],
        cfgs[5],
        cfgs[14],
        cfgs[15],
        cfgs[16],
        cfgs[1],
        cfgs[3],
        cfgs[0] or cfgs[2],
    )
    closes = calc_close_grid_short(
        cfgs[4],  # backwards_tp
        balance,
        (
            min(0.0, -abs(pos_short[0]) + abs(unstucking_close[0]))
            if unstucking_close[0]
            else pos_short[0]
        ),
        pos_short[1],
        close_price,  # close price
        min(emas),
        0,  # utc_now_ms: timed AU is disabled
        0,  # prev_AU_fill_ts_close: timed AU is disabled
        inverse,
        qty_step,
        price_step,
        min_qty,
        min_cost,
        c_mult,
        cfgs[16],  # wallet_exposure_limit
        cfgs[12],  # min_markup,
        cfgs[11],  # markup_range,
        cfgs[13],  # n_close_orders,
        cfgs[3],  # auto_unstuck_wallet_exposure_threshold,
        cfgs[1],  # auto_unstuck_ema_dist,
        cfgs[0],  # auto_unstuck_delay_minutes,
        cfgs[2],  # auto_unstuck_qty_pct,
    )
    if unstucking_close[0]:
        closes = [unstucking_close] + closes
    return entries, closes


@njit
def calc_fills(
    pside_idx,  # 0: long, 1: short
    k,
    poss_long,
    poss_short,
    idx,
    symbol,
    balance,
    entry,
    closes,
    hlc,
    inverse,
    qty_step,
    price_step,
    min_qty,
    min_cost,
    c_mults: np.ndarray,
    cfg: np.ndarray,
    maker_fee,
):
    """
    returns fills: [tuple], new_pos: (float, float), new_balance: float
    """
    fills = []
    pos = poss_long[idx] if pside_idx == 0 else poss_short[idx]
    new_pos = (pos[0], pos[1])
    new_balance = balance
    new_equity = new_balance + calc_pnl_sum(
        poss_long, poss_short, hlc[:, 1], hlc[:, 0], c_mults
    )  # compute total equity at this time step
    while entry[0] != 0.0 and (
        (pside_idx == 0 and hlc[idx][1] < entry[1]) or (pside_idx == 1 and hlc[idx][0] > entry[1])
    ):
        new_pos = calc_new_psize_pprice(
            new_pos[0],
            new_pos[1],
            entry[0],
            entry[1],
            qty_step,
        )
        fee_paid = -qty_to_cost(entry[0], entry[1], inverse, c_mults[idx]) * maker_fee
        new_balance = max(new_balance * 1e-6, new_balance + fee_paid)
        wallet_exposure = qty_to_cost(new_pos[0], new_pos[1], inverse, c_mults[idx]) / new_balance
        fills.append(
            (
                k,  # index
                symbol,
                0.0,  # realized pnl
                fee_paid,
                new_balance,
                new_equity,  # equity
                entry[0],  # fill qty
                entry[1],  # fill price
                new_pos[0],  # psize after fill
                new_pos[1],  # pprice after fill
                entry[2],  # fill type
                wallet_exposure / cfg[16],  # stuckness
            )
        )
        if "ientry" in entry[2]:
            break
        prev_eprice = entry[1]
        args = (
            new_balance,
            new_pos[0],
            new_pos[1],
            entry[1],
            entry[1],
            inverse,
            qty_step,
            price_step,
            min_qty,
            min_cost,
            c_mults[idx],
            cfg[10],
            cfg[9],
            cfg[5],
            cfg[14],
            cfg[15],
            cfg[16],
            cfg[1],
            cfg[3],
            cfg[0] or cfg[2],
        )
        if pside_idx == 0:
            entry = calc_recursive_entry_long(*args)
        else:
            entry = calc_recursive_entry_short(*args)
        if entry[1] == prev_eprice:
            break
    for close in closes:
        if (
            close[0] == 0.0
            or (pside_idx == 0 and close[1] >= hlc[idx][0])
            or (pside_idx == 1 and close[1] <= hlc[idx][1])
        ):
            break
        # close fill
        new_pos_ = (round_(new_pos[0] + close[0], qty_step), new_pos[1])
        if (pside_idx == 0 and new_pos_[0] < 0.0) or (pside_idx == 1 and new_pos_[0] > 0.0):
            print("warning: close qty greater than psize", "short" if pside_idx else "short")
            print("symbol", symbol)
            print("new_pos", new_pos)
            print("new_pos_", new_pos_)
            print("closes order", close)
            close = (-new_pos[0], close[1], close[2])
            new_pos_ = (0.0, 0.0)
        elif new_pos_[0] == 0.0:
            new_pos_ = (0.0, 0.0)
        fee_paid = -qty_to_cost(close[0], close[1], inverse, c_mults[idx]) * maker_fee
        pnl = (
            calc_pnl_long(new_pos[1], close[1], close[0], inverse, c_mults[idx])
            if pside_idx == 0
            else calc_pnl_short(new_pos[1], close[1], close[0], inverse, c_mults[idx])
        )
        new_pos = new_pos_
        new_balance = max(new_balance * 1e-6, new_balance + fee_paid + pnl)
        wallet_exposure = qty_to_cost(new_pos[0], new_pos[1], inverse, c_mults[idx]) / new_balance
        fills.append(
            (
                k,  # index
                symbol,
                pnl,  # realized pnl
                fee_paid,
                new_balance,  # post fill
                new_equity,  # post fill
                close[0],  # fill qty
                close[1],  # fill price
                new_pos[0],  # psize after fill
                new_pos[1],  # pprice after fill
                close[2],  # fill type
                wallet_exposure / cfg[16],  # stuckness
            )
        )

    return fills, new_pos, new_balance, new_equity


@njit
def calc_AU_allowance(
    pnls: np.ndarray, balance: float, loss_allowance_pct=0.01, drop_since_peak_abs=-1.0
):
    """
    allow up to 1% drop from balance peak for auto unstuck
    """
    if drop_since_peak_abs == -1.0:
        pnl_cumsum = pnls.cumsum()
        drop_since_peak_abs = pnl_cumsum.max() - pnl_cumsum[-1]
    balance_peak = balance + drop_since_peak_abs
    drop_since_peak_pct = balance / balance_peak - 1
    AU_allowance = max(0.0, balance_peak * (loss_allowance_pct + drop_since_peak_pct))
    return AU_allowance


@njit
def backtest_multisymbol_recursive_grid(
    hlcs,
    starting_balance,
    maker_fee,
    do_longs,
    do_shorts,
    c_mults,
    symbols,
    qty_steps,
    price_steps,
    min_costs,
    min_qtys,
    live_configs,
    loss_allowance_pct,
    stuck_threshold,
    unstuck_close_pct,
):
    """
    multi symbol backtest
    all symbols share same wallet

    interval is 1m
    hlcs: [[[sym1_high_0, sym1_low_0, sym1_close_0],
            [sym1_high_1, sym1_low_1, sym1_close_1],
            ...],
           [[sym1_high_0, sym1_low_0, sym1_close_0],
            [sym1_high_1, sym1_low_1, sym1_close_1],
            ...],
           ...
           ]
    # static values
    do_longs: (True, True, ...)
    do_shorts: (True, True, ...)
    c_mults: (float, float, ...)
    symbols: (str, str, ...)
    qty_steps: (float, float, ...)
    price_steps: (float, float, ...)
    min_costs: (float, float, ...)
    min_qtys: (float, float, ...)

    # each symbol has its own config
    # no dicts, structs, classes or named tubles allowed with numba...
    # a config is a list of float values
    # indices:

    0  auto_unstuck_delay_minutes
    1  auto_unstuck_ema_dist
    2  auto_unstuck_qty_pct
    3  auto_unstuck_wallet_exposure_threshold
    4  backwards_tp
    5  ddown_factor
    6  ema_span_0
    7  ema_span_1
    8  enabled
    9  initial_eprice_ema_dist
    10 initial_qty_pct
    11 markup_range
    12 min_markup
    13 n_close_orders
    14 rentry_pprice_dist
    15 rentry_pprice_dist_wallet_exposure_weighting
    16 wallet_exposure_limit

    live_configs: [((float, float, ...), (float, float, ...)), ((float, float, ...), (float, float, ...))]
    [(long, short), (long, short), ...]

    stuck_threshold: if WE / WE_limit > stuck_threshold: consider position stuck
    """

    inverse = False

    idxs = np.arange(len(symbols))

    ll = [[z[0] for z in x] for x in live_configs]  # live configs long
    ls = [[z[1] for z in x] for x in live_configs]  # live configs short
    # disable auto unstuck
    ll = [[0.0] * 4 + x[4:] for x in ll]
    ls = [[0.0] * 4 + x[4:] for x in ls]

    balance = starting_balance
    poss_long = [(0.0, 0.0) for _ in range(len(symbols))]  # [psize: float, pprice: float]
    poss_short = [(0.0, 0.0) for _ in range(len(symbols))]  # [psize: float, pprice: float]
    fills = []
    stats = [
        (
            0,
            poss_long.copy(),
            poss_short.copy(),
            hlcs[:, 0, 2],
            balance,
            balance,
        )
    ]
    entries_long = [(0.0, 0.0, "") for _ in idxs]  # (qty: float, price: float, type: str)
    entries_short = [(0.0, 0.0, "") for _ in idxs]
    closes_long = [[(0.0, 0.0, "")] for _ in idxs]  # [(qty: float, price: float, type: str), (), ...]
    closes_short = [
        [(0.0, 0.0, "")] for _ in idxs
    ]  # [(qty: float, price: float, type: str), (), ...]

    ema_spans_long = [np.array(sorted((x[6], (x[6] * x[7]) ** 0.5, x[7]))) for x in ll]
    ema_spans_short = [np.array(sorted((x[6], (x[6] * x[7]) ** 0.5, x[7]))) for x in ls]
    ema_spans_long = [np.where(x < 1.0, 1.0, x) for x in ema_spans_long]
    ema_spans_short = [np.where(x < 1.0, 1.0, x) for x in ema_spans_short]

    # find first non zero hlcs
    first_non_zero_idxs = [0 for _ in idxs]
    for i in idxs:
        for k in range(len(hlcs[i])):
            if hlcs[i][k][2] != 0.0:
                first_non_zero_idxs[i] = k
                break
    emas_long = [np.repeat(hlcs[i][k][2], 3) for i, k in enumerate(first_non_zero_idxs)]
    emas_short = [np.repeat(hlcs[i][k][2], 3) for i, k in enumerate(first_non_zero_idxs)]

    alphas_long = [2.0 / (x + 1.0) for x in ema_spans_long]
    alphas__long = [1.0 - x for x in alphas_long]
    alphas_short = [2.0 / (x + 1.0) for x in ema_spans_short]
    alphas__short = [1.0 - x for x in alphas_short]
    any_do_long, any_do_short = False, False
    idxs_long, idxs_short = [], []
    for i in range(len(do_longs)):
        if do_longs[i] and ll[i][16] > 0.0:  # long enabled and long WE_limit > 0.0
            idxs_long.append(i)
            any_do_long = True
    for i in range(len(do_shorts)):
        if do_shorts[i] and ls[i][16] > 0.0:
            idxs_short.append(i)
            any_do_short = True

    stuck_positions_long = np.zeros(len(symbols))  # 0 is unstuck; 1 is stuck
    stuck_positions_short = np.zeros(len(symbols))  # 0 is unstuck; 1 is stuck

    unstucking_close = (0.0, 0.0, "")
    s_i, s_pside = -1, -1

    bankrupt = False
    any_stuck = False
    pnl_cumsum_running = 0.0
    pnl_cumsum_max = 0.0

    for k in range(1, len(hlcs[0])):
        any_fill = False

        # check for fills long
        for i in idxs_long:
            if hlcs[i][k][0] == 0.0:
                continue
            emas_long[i] = calc_ema(alphas_long[i], alphas__long[i], emas_long[i], hlcs[i][k][2])
            if (entries_long[i][0] > 0.0 and hlcs[i][k][1] < entries_long[i][1]) or (
                poss_long[i][0] > 0.0
                and closes_long[i][0][0] != 0.0
                and hlcs[i][k][0] > closes_long[i][0][1]
            ):
                # there were fills
                new_fills, new_pos_long, new_balance, new_equity = calc_fills(
                    0,
                    k,
                    poss_long,
                    poss_short,
                    i,
                    symbols[i],
                    balance,
                    entries_long[i],
                    closes_long[i],
                    hlcs[:, k],
                    inverse,
                    qty_steps[i],
                    price_steps[i],
                    min_qtys[i],
                    min_costs[i],
                    c_mults,
                    ll[i],
                    maker_fee,
                )
                if len(new_fills) > 0:
                    any_fill = True
                if new_equity / new_balance < 0.1:
                    bankrupt = True
                for fill in new_fills:
                    pnl_cumsum_running += fill[2]
                    pnl_cumsum_max = max(pnl_cumsum_max, pnl_cumsum_running)
                fills.extend(new_fills)
                poss_long[i] = new_pos_long
                balance = new_balance

                wallet_exposure = (
                    qty_to_cost(poss_long[i][0], poss_long[i][1], inverse, c_mults[i]) / balance
                )
                if loss_allowance_pct > 0.0 and wallet_exposure / ll[i][16] > stuck_threshold:
                    any_stuck = True
                    stuck_positions_long[i] = 1.0
                else:
                    # is unstuck
                    stuck_positions_long[i] = 0.0

        # check for fills short
        for i in idxs_short:
            if hlcs[i][k][0] == 0.0:
                continue
            emas_short[i] = calc_ema(alphas_short[i], alphas__short[i], emas_short[i], hlcs[i][k][2])
            if (entries_short[i][0] != 0.0 and hlcs[i][k][0] > entries_short[i][1]) or (
                poss_short[i][0] != 0.0
                and closes_short[i][0][0] != 0.0
                and hlcs[i][k][1] < closes_short[i][0][1]
            ):
                # there were fills
                new_fills, new_pos_short, new_balance, new_equity = calc_fills(
                    1,
                    k,
                    poss_long,
                    poss_short,
                    i,
                    symbols[i],
                    balance,
                    entries_short[i],
                    closes_short[i],
                    hlcs[:, k],
                    inverse,
                    qty_steps[i],
                    price_steps[i],
                    min_qtys[i],
                    min_costs[i],
                    c_mults,
                    ls[i],
                    maker_fee,
                )
                if len(new_fills) > 0:
                    any_fill = True
                if new_equity / new_balance < 0.1:
                    bankrupt = True
                for fill in new_fills:
                    pnl_cumsum_running += fill[2]
                    pnl_cumsum_max = max(pnl_cumsum_max, pnl_cumsum_running)
                fills.extend(new_fills)
                poss_short[i] = new_pos_short
                balance = new_balance

                wallet_exposure = (
                    qty_to_cost(poss_short[i][0], poss_short[i][1], inverse, c_mults[i]) / balance
                )
                if loss_allowance_pct > 0.0 and wallet_exposure / ls[i][16] > stuck_threshold:
                    any_stuck = True
                    stuck_positions_short[i] = 1.0
                else:
                    # is unstuck
                    stuck_positions_short[i] = 0.0

        s_i, s_pside = -1, -1
        unstucking_close = (0.0, 0.0, "")
        if any_stuck:
            # check if all are unstuck
            any_stuck = False
            for idx in idxs_long:
                if stuck_positions_long[idx]:
                    any_stuck = True
                    break
            for idx in idxs_short:
                if stuck_positions_short[idx]:
                    any_stuck = True
                    break

            if any_stuck:
                # find which position to unstuck
                # lowest pprice diff is chosen
                s_pside = 0  # 0==long, 1==short
                s_i = -1  # index
                lowest_pprice_diff = 100.0
                for i in idxs_long:
                    if stuck_positions_long[i]:
                        # long is stuck
                        if hlcs[i][k][2] > poss_long[i][1]:
                            continue  # no unstucking if price > pprice
                        pprice_diff = 1.0 - hlcs[i][k][2] / poss_long[i][1]
                        if pprice_diff < lowest_pprice_diff:
                            lowest_pprice_diff = pprice_diff
                            s_i = i
                            s_pside = 0
                for i in idxs_short:
                    if stuck_positions_short[i]:
                        # short is stuck
                        if hlcs[i][k][2] < poss_short[i][1]:
                            continue  # no unstucking if price < pprice
                        pprice_diff = hlcs[i][k][2] / poss_short[i][1] - 1.0
                        if pprice_diff < lowest_pprice_diff:
                            lowest_pprice_diff = pprice_diff
                            s_i = i
                            s_pside = 1
                AU_allowance = calc_AU_allowance(
                    np.array([0.0]),
                    balance,
                    loss_allowance_pct=loss_allowance_pct,
                    drop_since_peak_abs=(pnl_cumsum_max - pnl_cumsum_running),
                )
                if AU_allowance > 0.0 and s_i != -1:
                    if s_pside:  # short
                        close_price = min(
                            hlcs[s_i][k][2], round_dn(emas_short[s_i].min(), price_steps[s_i])
                        )  # lower ema band
                        upnl = calc_pnl_short(
                            poss_short[s_i][1],
                            hlcs[s_i][k][2],
                            poss_short[s_i][0],
                            inverse,
                            c_mults[s_i],
                        )
                        AU_allowance_pct = 1.0 if upnl >= 0.0 else min(1.0, AU_allowance / abs(upnl))
                        AU_allowance_qty = round_(
                            abs(poss_short[s_i][0]) * AU_allowance_pct, qty_steps[s_i]
                        )
                        close_qty = max(
                            calc_min_entry_qty(
                                close_price,
                                inverse,
                                c_mults[s_i],
                                qty_steps[s_i],
                                min_qtys[s_i],
                                min_costs[s_i],
                            ),
                            min(
                                abs(AU_allowance_qty),
                                round_(
                                    cost_to_qty(
                                        balance * ls[s_i][16] * unstuck_close_pct,
                                        close_price,
                                        inverse,
                                        c_mults[s_i],
                                    ),
                                    qty_steps[s_i],
                                ),
                            ),
                        )
                        unstucking_close = (
                            min(abs(close_qty), abs(poss_short[s_i][0])),
                            close_price,
                            "unstuck_close_short",
                        )
                    else:  # long
                        close_price = max(
                            hlcs[s_i][k][2], round_up(emas_long[s_i].max(), price_steps[s_i])
                        )  # upper ema band
                        upnl = calc_pnl_long(
                            poss_long[s_i][1],
                            hlcs[s_i][k][2],
                            poss_long[s_i][0],
                            inverse,
                            c_mults[s_i],
                        )
                        AU_allowance_pct = 1.0 if upnl >= 0.0 else min(1.0, AU_allowance / abs(upnl))
                        AU_allowance_qty = round_(
                            abs(poss_long[s_i][0]) * AU_allowance_pct, qty_steps[s_i]
                        )
                        close_qty = max(
                            calc_min_entry_qty(
                                close_price,
                                inverse,
                                c_mults[s_i],
                                qty_steps[s_i],
                                min_qtys[s_i],
                                min_costs[s_i],
                            ),
                            min(
                                abs(AU_allowance_qty),
                                round_(
                                    cost_to_qty(
                                        balance * ll[s_i][16] * unstuck_close_pct,
                                        close_price,
                                        inverse,
                                        c_mults[s_i],
                                    ),
                                    qty_steps[s_i],
                                ),
                            ),
                        )
                        unstucking_close = (
                            -min(abs(close_qty), abs(poss_long[s_i][0])),
                            close_price,
                            "unstuck_close_long",
                        )

        # check if open orders long need to be updated
        for i in idxs_long:
            if hlcs[i][k][0] == 0.0:
                continue
            if (
                any_fill
                or poss_long[i][0] == 0.0
                or (s_pside == 0 and s_i == i and unstucking_close[0])
            ):
                # calc orders if any fill or if psize is zero or if stuck
                entries_long[i], closes_long[i] = get_open_orders_long(
                    hlcs[i][k][2],
                    balance,
                    poss_long[i],
                    emas_long[i],
                    unstucking_close if s_pside == 0 and s_i == i else (0.0, 0.0, ""),
                    inverse,
                    qty_steps[i],
                    price_steps[i],
                    min_qtys[i],
                    min_costs[i],
                    c_mults[i],
                    ll[i],
                )

        # check if open orders short need to be updated
        for i in idxs_short:
            if hlcs[i][k][0] == 0.0:
                continue
            if (
                any_fill
                or poss_short[i][0] == 0.0
                or (unstucking_close[0] and s_pside == 1 and s_i == i)
            ):
                # calc orders if any fill or if psize is zero or if stuck
                entries_short[i], closes_short[i] = get_open_orders_short(
                    hlcs[i][k][2],
                    balance,
                    poss_short[i],
                    emas_short[i],
                    unstucking_close if s_pside == 1 and s_i == i else (0.0, 0.0, ""),
                    inverse,
                    qty_steps[i],
                    price_steps[i],
                    min_qtys[i],
                    min_costs[i],
                    c_mults[i],
                    ls[i],
                )

        if k % 60 == 0:
            # update stats hourly
            equity = balance + calc_pnl_sum(
                poss_long, poss_short, hlcs[:, k, 1], hlcs[:, k, 0], c_mults
            )
            stats.append(
                (
                    k,
                    poss_long.copy(),
                    poss_short.copy(),
                    hlcs[:, k, 2],
                    balance,
                    equity,
                )
            )
            if equity / balance < 0.1 or bankrupt:
                # bankrupt
                bankrupt = True
                break
    equity = balance + calc_pnl_sum(poss_long, poss_short, hlcs[:, k, 1], hlcs[:, k, 0], c_mults)
    if bankrupt:
        # force equity to be close to zero if bankrupt
        stats.append(
            (
                stats[-1][0] + 60,
                poss_long.copy(),
                poss_short.copy(),
                hlcs[:, k, 2],
                balance,
                min(starting_balance * 1e-12, equity),
            )
        )
    elif stats[-1][0] != k:
        stats.append(
            (
                stats[-1][0] + 60,
                poss_long.copy(),
                poss_short.copy(),
                hlcs[:, k, 2],
                balance,
                equity,
            )
        )
    return fills, stats


@njit
def backtest_fast_recursive(
    hlcs,
    starting_balance,
    maker_fee,
    qty_step,
    price_step,
    min_qty,
    min_cost,
    initial_qty_pct,
    wallet_exposure_limit,
    ddown_factor,
    rentry_pprice_dist,
    rentry_pprice_dist_wallet_exposure_weighting,
    min_markup,
):
    # assume initial entry at first hlc close price
    # break loop if position closes
    # break loop if bankrupt
    # break loop if pprice diff > threshold
    pos_long = (
        round_(
            cost_to_qty(
                starting_balance * wallet_exposure_limit * initial_qty_pct, hlcs[0][2], False, 1.0
            ),
            qty_step,
        ),
        hlcs[0][2],
    )
    entry_long = calc_recursive_entry_long(
        starting_balance,
        pos_long[0],
        pos_long[1],
        pos_long[1],
        pos_long[1],
        False,
        qty_step,
        price_step,
        min_qty,
        min_cost,
        1.0,
        initial_qty_pct,
        0.0,
        ddown_factor,
        rentry_pprice_dist,
        rentry_pprice_dist_wallet_exposure_weighting,
        wallet_exposure_limit,
        0.0,
        0.0,
        False,
    )
    close_long = (pos_long[0], round_up(pos_long[1] * (1 + min_markup), price_step))
    bkr_price = calc_bankruptcy_price(
        starting_balance, pos_long[0], pos_long[1], 0.0, 0.0, False, 1.0
    )
    pprice_diff_threshold_pct = 0.25  # max 25% pos price diff
    pprice_diff_threshold = pos_long[1] * (1 - pprice_diff_threshold_pct)
    fills = [(0, 0.0, bkr_price, pos_long[0], pos_long[1], "ientry_long")]

    for k in range(1, len(hlcs)):
        # check for fills
        if hlcs[k][0] > close_long[1]:
            pnl = calc_pnl_long(pos_long[1], close_long[1], close_long[0], False, 1.0)
            fills.append((k, pnl, 0.0, close_long[0], close_long[1], "close_long"))
            return fills
        if hlcs[k][1] < entry_long[1]:
            n_psize = round_(pos_long[0] + entry_long[0], qty_step)
            n_pprice = pos_long[1] * (pos_long[0] / n_psize) + entry_long[1] * (
                entry_long[0] / n_psize
            )
            bkr_price = calc_bankruptcy_price(
                starting_balance, n_psize, n_pprice, 0.0, 0.0, False, 1.0
            )
            fills.append((k, 0.0, bkr_price, entry_long[0], entry_long[1], "rentry_long"))
            pos_long = (n_psize, n_pprice)
            entry_long = calc_recursive_entry_long(
                starting_balance,
                pos_long[0],
                pos_long[1],
                pos_long[1],
                pos_long[1],
                False,
                qty_step,
                price_step,
                min_qty,
                min_cost,
                1.0,
                initial_qty_pct,
                0.0,
                ddown_factor,
                rentry_pprice_dist,
                rentry_pprice_dist_wallet_exposure_weighting,
                wallet_exposure_limit,
                0.0,
                0.0,
                False,
            )
            close_long = (pos_long[0], round_up(pos_long[1] * (1 + min_markup), price_step))
            pprice_diff_threshold = pos_long[1] * (1 - pprice_diff_threshold_pct)
        if hlcs[k][1] <= bkr_price:
            fills.append((k, -starting_balance, 0.0, pos_long[0], hlcs[k][1], "liquidation_long"))
            return fills
        if hlcs[k][1] <= pprice_diff_threshold:
            fills.append((k, 0.0, bkr_price, 0.0, hlcs[k][1], "pprice diff break"))
            return fills
    return fills


@njit
def make_buckets(hlcs, bucket_size=15):
    num_buckets = int(np.ceil(hlcs.shape[0] / bucket_size))
    bucketed = np.zeros((num_buckets, hlcs.shape[1], hlcs.shape[2]))

    for i in range(num_buckets):
        start = i * bucket_size
        end = (i + 1) * bucket_size
        bucket = hlcs[start:end]

        for j in range(hlcs.shape[1]):
            bucketed[i, j, 0] = np.max(bucket[:, j, 0])
            bucketed[i, j, 1] = np.min(bucket[:, j, 1])

        bucketed[i, :, 2] = bucket[-1, :, 2]

    return bucketed


@njit
def calc_NRR(hlcs):
    # returns normalized relative range
    # (high - low) / close
    nrr = np.zeros(hlcs.shape[:2])
    for i in range(hlcs.shape[0]):
        for j in range(hlcs.shape[1]):
            if hlcs[i, j][2] != 0.0:
                nrr[i, j] = (hlcs[i, j][0] - hlcs[i, j][1]) / hlcs[i, j][2]
            else:
                nrr[i, j] = 0.0
    return nrr


@njit
def calc_rolling_mean(data, window=100):
    """
    Calculate the rolling mean of a 1D array with a specified window size, handling cases where data
    length is less than the window size.

    Args:
        data (np.array): 1D numpy array of floats.
        window (int): Size of the rolling window.

    Returns:
        np.array: 1D array containing the rolling mean values, same length as input data.
    """
    n = len(data)
    result = np.empty(data.shape)
    window_sums = np.zeros(data.shape[1])
    window_count = 0

    # Initialize the sum and count for the first window
    for i in range(min(window, n)):
        window_sums += data[i]
        window_count += 1
        result[i] = window_sums / window_count

    # Calculate rolling mean for the rest of the data
    for i in range(window, n):
        window_sums += data[i] - data[i - window]
        result[i] = window_sums / window

    return result


@njit
def custom_repeat_rows(arr, n):
    """
    Custom repeat function to repeat each row of a 2D array `n` times.
    """
    repeated_arr = np.empty((arr.shape[0] * n, arr.shape[1]), dtype=arr.dtype)
    for i in range(arr.shape[0]):
        for j in range(n):
            repeated_arr[i * n + j] = arr[i]
    return repeated_arr


@njit
def repeat_elements_to_rows(arr, n):
    """
    Custom repeat function to repeat each element of a 1D array `n` times into separate rows.
    """
    result = np.empty((arr.shape[0], n), dtype=arr.dtype)
    for i in range(arr.shape[0]):
        for j in range(n):
            result[i, j] = arr[i]
    return result


@njit
def multiply_arrays(arr0, arr1):
    result = np.empty((len(arr1), len(arr0)))
    for i in range(len(arr1)):
        for j in range(len(arr0)):
            result[i, j] = arr1[i] * arr0[j]
    return result


@njit
def calc_noisiness_argsort_indices(hlcs, bucket_size=15, rolling_window=100):
    bucketed = make_buckets(hlcs, bucket_size)  # bucket into bucket_size
    noisiness = calc_NRR(bucketed)  # compute normalized relative range for each bucket
    rolling_mean = calc_rolling_mean(noisiness, rolling_window)  # rolling mean
    expanded = custom_repeat_rows(rolling_mean, bucket_size)  # expand to same length as hlcs
    # repeat first bucket as front padding
    front_padding = custom_repeat_rows(expanded[:1], bucket_size)
    # shift forwards, clip last buckets
    shifted = np.concatenate((front_padding, expanded[:-bucket_size]))
    return reverse_sorted_indices_parallel(shifted)  # return reverse argsort for each timestep


@njit
def calc_next_ema_multiple(alphas, alphas_, emas, closes):
    return multiply_arrays(alphas, closes) + alphas_ * emas


@njit
def prepare_emas_forager(spans_long, spans_short, hlcs_first):
    """
    spans: [span0, span1]
    """
    spans_long = [spans_long[0], spans_long[1], (spans_long[0] * spans_long[1]) ** 0.5]
    spans_long = np.array(sorted(spans_long))
    spans_short = [spans_short[0], spans_short[1], (spans_short[0] * spans_short[1]) ** 0.5]
    spans_short = np.array(sorted(spans_short))
    spans_long = np.where(spans_long < 1.0, 1.0, spans_long)
    spans_short = np.where(spans_short < 1.0, 1.0, spans_short)
    emas_long = repeat_elements_to_rows(hlcs_first[:, 2], 3)
    emas_short = repeat_elements_to_rows(hlcs_first[:, 2], 3)
    alphas_long = 2.0 / (spans_long + 1.0)
    alphas__long = 1.0 - alphas_long
    alphas_short = 2.0 / (spans_short + 1.0)
    alphas__short = 1.0 - alphas_short
    return emas_long, emas_short, alphas_long, alphas__long, alphas_short, alphas__short


@njit
def reverse_sorted_indices_parallel(arr):
    x, y = arr.shape
    sorted_indices_arr = np.empty((x, y), dtype=np.int64)

    for i in range(x):
        row = arr[i, :]
        indices = np.arange(y)
        sorted_indices = np.empty(y, dtype=np.int64)

        # Perform reverse argsort manually to avoid overhead
        for j in range(y):
            for k in range(j + 1, y):
                if row[indices[j]] < row[indices[k]]:
                    indices[j], indices[k] = indices[k], indices[j]

        sorted_indices[:] = indices
        sorted_indices_arr[i, :] = sorted_indices

    return sorted_indices_arr


def precompute_noisiest_indices(hlcs, bucket_size=15, rolling_window=100):
    noisiness = calc_noisiness(hlcs, bucket_size, rolling_window)
    sorted_indices = reverse_sorted_indices_parallel(noisiness)


@njit
def backtest_forager(
    hlcs,
    noisiness_indices,  # noisiness for all symbols, argsort for each timestep
    starting_balance,
    maker_fee,
    c_mults,
    symbols,
    qty_steps,
    price_steps,
    min_costs,
    min_qtys,
    forager_live_config,
):
    """
    hlcs contains all eligible symbols, time frame is 1m
    hlcs array shape: (n_minutes, n_markets, 3):
    [
        [
            [sym0_high0, sym0_low0, sym0_close0, ...],
            [sym1_high0, sym1_low0, sym1_close0, ...],
            [sym2_high0, sym2_low0, sym2_close0, ...],
            ...
        ],
        [
            [sym0_high1, sym0_low1, sym0_close1, ...],
            [sym1_high1, sym1_low1, sym1_close1, ...],
            [sym2_high1, sym2_low1, sym2_close1, ...],
            ...
        ],
        [
            [sym0_high2, sym0_low2, sym0_close2, ...],
            [sym1_high2, sym1_low2, sym1_close2, ...],
            [sym2_high2, sym2_low2, sym2_close2, ...],
            ...
        ],
        ...
    ]

    noisiness is rolling mean normalized relative range [(high - low) / close]
    timeframe is higher than 1m, e.g. 15m
    noisiness array is shifted forwards by timeframe n, so at time step k, noisiness[k] is mean noisiness [k - n : k]
    noisiness_indices is argsort for each timestep
    noisiness_indices array shape: (n_minutes, n_markets):
    [
        [sym0_noise0, sym1_noise0, sym2_noise0, ...],
        [sym0_noise1, sym1_noise1, sym2_noise1, ...],
        [sym0_noise2, sym1_noise2, sym2_noise2, ...],
        ...
    ]


    forager_live_config structure:
    [
        [
            0 long_ddown_factor,
            1 long_ema_span_0,
            2 long_ema_span_1,
            3 long_initial_eprice_ema_dist,
            4 long_initial_qty_pct,
            5 long_markup_range,
            6 long_min_markup,
            7 long_n_close_orders,
            8 long_n_positions,
            9 long_rentry_pprice_dist,
            10 long_rentry_pprice_dist_wallet_exposure_weighting,
            11 long_total_wallet_exposure_limit,
            12 long_unstuck_close_pct,
            13 long_unstuck_ema_dist,
            14 long_unstuck_loss_allowance_pct,
            15 long_unstuck_threshold,
        ],
        [
            0 short_ddown_factor,
            1 short_ema_span_0,
            2 short_ema_span_1,
            3 short_initial_eprice_ema_dist,
            4 short_initial_qty_pct,
            5 short_markup_range,
            6 short_min_markup,
            7 short_n_close_orders,
            8 short_n_positions,
            9 short_rentry_pprice_dist,
            10 short_rentry_pprice_dist_wallet_exposure_weighting,
            11 short_total_wallet_exposure_limit,
            12 short_unstuck_close_pct,
            13 short_unstuck_ema_dist,
            14 short_unstuck_loss_allowance_pct,
            15 short_unstuck_threshold,
    ]
    """
    flc = forager_live_config
    balance = starting_balance
    inverse = False
    backwards_tp = True
    auto_unstuck_ema_dist = 0.0
    auto_unstuck_wallet_exposure_threshold = 0.0
    auto_unstuck_delay_minutes = 0.0
    auto_unstuck_qty_pct = 0.0
    auto_unstuck_on_timer = False
    enabled_long = flc[0][8] > 0
    enabled_short = flc[1][8] > 0
    wallet_exposure_limit_long = flc[0][11] / flc[0][8] if enabled_long else 0.0
    wallet_exposure_limit_short = flc[1][11] / flc[1][8] if enabled_short else 0.0
    emas_long, emas_short, alphas_long, alphas__long, alphas_short, alphas__short = (
        prepare_emas_forager([flc[0][1], flc[0][2]], [flc[1][1], flc[1][2]], hlcs[0])
    )

    assert len(symbols) == len(hlcs[0]), "length mismatch symbols, hlcs"

    positions_long = np.zeros((len(hlcs[0]), 2)) if enabled_long else np.zeros((0, 2))
    positions_short = np.zeros((len(hlcs[0]), 2)) if enabled_short else np.zeros((0, 2))

    has_pos_long = set()
    has_pos_short = set()

    is_stuck_long = set()
    is_stuck_short = set()

    active_longs = set()
    active_shorts = set()

    open_orders_entry_long = []
    open_orders_close_long = []
    open_orders_entry_short = []
    open_orders_close_short = []

    unstuck_order = (0, 0, (0.0, 0.0, ""))
    unstuck_closes = [(0.0, 0.0, "")]
    pnl_cumsum_max = 0.0
    pnl_cumsum_running = 0.0

    fills = []
    stats = [
        (
            0,  # minute
            positions_long.copy(),
            positions_short.copy(),
            hlcs[0, :, 2],  # high, low, close at timestep
            balance,  # balance
            balance,  # equity
        )
    ]

    #########
    # to remove:for numba type inference
    has_pos_short.add(5)
    has_pos_short.remove(5)
    is_stuck_long.add(1)
    is_stuck_long.remove(1)
    is_stuck_short.add(1)
    is_stuck_short.remove(1)
    active_longs.add(1)
    active_longs.remove(1)
    active_shorts.add(1)
    active_shorts.remove(1)
    fills.append(
        (
            0,  # index minute
            "none",  # symbol
            0.0,  # realized pnl
            0.0,  # fee paid
            0.0,  # balance after fill
            0.0,  # equity
            0.0,  # fill qty
            0.0,  # fill price
            0.0,  # psize after fill
            0.0,  # pprice after fill
            "none",  # fill type
            0.0,  # stuckness
        )
    )
    fills = fills[:0]

    open_orders_entry_long.append((3, [(1.0, 1.0, "none")]))
    open_orders_close_long.append((3, [(1.0, 1.0, "none")]))
    open_orders_entry_short.append((3, [(1.0, 1.0, "none")]))
    open_orders_close_short.append((3, [(1.0, 1.0, "none")]))

    open_orders_entry_long = open_orders_entry_long[:0]
    open_orders_close_long = open_orders_close_long[:0]
    open_orders_entry_short = open_orders_entry_short[:0]
    open_orders_close_short = open_orders_close_short[:0]

    #######

    for k in range(1, len(hlcs)):
        any_fill = False
        if enabled_long:
            # calc emas
            emas_long = calc_next_ema_multiple(alphas_long, alphas__long, emas_long, hlcs[k, :, 2])
            # check for fills
            for idx, entries in open_orders_entry_long:
                for entry in entries:
                    if hlcs[k][idx][1] < entry[1] and entry[0] != 0.0:
                        # long entry fill
                        any_fill = True
                        has_pos_long.add(idx)
                        fee_paid = -qty_to_cost(entry[0], entry[1], inverse, c_mults[idx]) * maker_fee
                        balance += fee_paid
                        equity = balance + calc_pnl_sum(
                            positions_long, positions_short, hlcs[k, :, 1], hlcs[k, :, 0], c_mults
                        )
                        positions_long[idx][0], positions_long[idx][1] = calc_new_psize_pprice(
                            positions_long[idx][0],
                            positions_long[idx][1],
                            entry[0],
                            entry[1],
                            qty_steps[idx],
                        )
                        wallet_exposure = (
                            qty_to_cost(
                                positions_long[idx][0], positions_long[idx][1], inverse, c_mults[idx]
                            )
                            / balance
                        )
                        stuckness = wallet_exposure / wallet_exposure_limit_long
                        if flc[0][14] != 0.0 and stuckness > flc[0][15]:
                            is_stuck_long.add(idx)
                        elif idx in is_stuck_long:
                            is_stuck_long.remove(idx)
                        fills.append(
                            (
                                k,  # index minute
                                symbols[idx],  # symbol
                                0.0,  # realized pnl
                                fee_paid,  # fee paid
                                balance,  # balance after fill
                                equity,  # equity
                                entry[0],  # fill qty
                                entry[1],  # fill price
                                positions_long[idx][0],  # psize after fill
                                positions_long[idx][1],  # pprice after fill
                                entry[2],  # fill type
                                stuckness,  # stuckness
                            )
                        )
            for idx, closes in open_orders_close_long:
                for close in closes:
                    if close[0] == 0.0:
                        continue
                    if hlcs[k][idx][0] > close[1]:
                        # long close fill
                        any_fill = True
                        new_psize = round_(positions_long[idx][0] + close[0], qty_steps[idx])
                        if new_psize < 0.0:
                            print("warning: close qty greater than psize long")
                            print("symbol", symbols[idx])
                            print("new_psize", new_psize)
                            print("close order", close)
                            new_psize = 0.0
                            close = (-positions_long[idx][0], close[1], close[2])
                        fee_paid = -qty_to_cost(close[0], close[1], inverse, c_mults[idx]) * maker_fee
                        pnl = calc_pnl_long(
                            positions_long[idx][1], close[1], close[0], inverse, c_mults[idx]
                        )
                        pnl_cumsum_running += pnl
                        pnl_cumsum_max = max(pnl_cumsum_max, pnl_cumsum_running)
                        balance += pnl + fee_paid
                        equity = balance + calc_pnl_sum(
                            positions_long, positions_short, hlcs[k, :, 1], hlcs[k, :, 0], c_mults
                        )
                        positions_long[idx][0] = new_psize
                        if new_psize == 0.0:
                            has_pos_long.remove(idx)
                            positions_long[idx][1] = 0.0
                        wallet_exposure = (
                            qty_to_cost(
                                positions_long[idx][0], positions_long[idx][1], inverse, c_mults[idx]
                            )
                            / balance
                        )
                        stuckness = wallet_exposure / wallet_exposure_limit_long
                        if flc[0][14] != 0.0 and stuckness > flc[0][15]:
                            is_stuck_long.add(idx)
                        elif idx in is_stuck_long:
                            is_stuck_long.remove(idx)
                        fills.append(
                            (
                                k,  # index minute
                                symbols[idx],  # symbol
                                pnl,  # realized pnl
                                fee_paid,  # fee paid
                                balance,  # balance after fill
                                equity,  # equity
                                close[0],  # fill qty
                                close[1],  # fill price
                                positions_long[idx][0],  # psize after fill
                                positions_long[idx][1],  # pprice after fill
                                close[2],  # fill type
                                stuckness,  # stuckness
                            )
                        )
                    else:
                        break
        if enabled_short:
            pass
            """
            emas_short = calc_next_ema_multiple(
                alphas_short, alphas__short, emas_short, hlcs[k, :, 2]
            )
            for ixs in is_active_short:
                if hlcs[k][ixs][1] < open_orders_close_short[ixs][0][1]:
                    # short close fill
                    any_fill = True
                if hlcs[k][ixs][0] > open_orders_entry_short[ixs][1]:
                    # short entry fill
                    any_fill = True
            """

        if any_fill:
            # update unstuck order
            unstuck_pside, unstuck_idx, unstuck_order = calc_unstuck_order(
                c_mults,
                qty_steps,
                price_steps,
                min_costs,
                min_qtys,
                flc,
                wallet_exposure_limit_long,
                wallet_exposure_limit_short,
                balance,
                pnl_cumsum_max - pnl_cumsum_running,
                is_stuck_long,
                is_stuck_short,
                positions_long,
                positions_short,
                emas_long,
                emas_short,
                hlcs[k],
            )
            # update all open orders
            if enabled_long:
                open_orders_entry_long = open_orders_entry_long[:0]
                open_orders_close_long = open_orders_close_long[:0]
                active_longs = set(has_pos_long)
                if len(active_longs) < flc[0][8]:
                    for x in noisiness_indices[k]:
                        if len(active_longs) >= flc[0][8]:
                            break
                        active_longs.add(x)
                for idx in active_longs:
                    entry = calc_recursive_entry_long(
                        balance,
                        positions_long[idx][0],
                        positions_long[idx][1],
                        hlcs[k - 1][idx][2],  # close of previous candle as highest_bid
                        min(emas_long[idx]),
                        inverse,
                        qty_steps[idx],
                        price_steps[idx],
                        min_qtys[idx],
                        min_costs[idx],
                        c_mults[idx],
                        flc[0][4],
                        flc[0][3],
                        flc[0][0],
                        flc[0][9],
                        flc[0][10],
                        wallet_exposure_limit_long,
                        auto_unstuck_ema_dist,
                        auto_unstuck_wallet_exposure_threshold,
                        auto_unstuck_on_timer,
                    )
                    open_orders_entry_long.append((idx, [entry]))
                    closes = []
                    if unstuck_pside == 0 and unstuck_idx == idx and unstuck_order[0] != 0.0:
                        closes.append(unstuck_order)
                        psize_adj = positions_long[idx][0] + unstuck_order[0]
                    else:
                        psize_adj = positions_long[idx][0]
                    for close in calc_close_grid_long(
                        backwards_tp,
                        balance,
                        psize_adj,
                        positions_long[idx][1],
                        hlcs[k - 1][idx][2],  # close of previous candle as lowest_ask
                        max(emas_long[idx]),
                        0.0,
                        0.0,
                        inverse,
                        qty_steps[idx],
                        price_steps[idx],
                        min_qtys[idx],
                        min_costs[idx],
                        c_mults[idx],
                        wallet_exposure_limit_long,
                        flc[0][6],
                        flc[0][5],
                        flc[0][7],
                        auto_unstuck_wallet_exposure_threshold,
                        auto_unstuck_ema_dist,
                        auto_unstuck_delay_minutes,
                        auto_unstuck_qty_pct,
                    ):
                        closes.append(close)
                    closes = [
                        c for c in sorted(closes, key=lambda x: x[1])
                    ]  # sort ascending by price
                    open_orders_close_long.append((idx, closes))

            if enabled_short:
                open_orders_entry_short = open_orders_entry_short[:0]
                open_orders_close_short = open_orders_close_short[:0]
                # active_shorts = calc_actives(flc[1][8], has_pos_short, noisiness_indices[k])
        else:
            # update only EMA based orders
            if enabled_long:
                if len(has_pos_long) < flc[0][8]:
                    # One or more empty slots; recalculate actives by noisiness
                    active_longs = set(has_pos_long)
                    for x in noisiness_indices[k]:
                        if len(active_longs) >= flc[0][8]:
                            break
                        active_longs.add(x)
                    new_open_orders_entry_long = []
                    for x in open_orders_entry_long:
                        if x[0] in has_pos_long:
                            new_open_orders_entry_long.append(x)
                    for idx in active_longs:
                        if idx not in has_pos_long:
                            entry = calc_recursive_entry_long(
                                balance,
                                positions_long[idx][0],
                                positions_long[idx][1],
                                hlcs[k - 1][idx][2],  # close of previous candle as highest_bid
                                min(emas_long[idx]),
                                inverse,
                                qty_steps[idx],
                                price_steps[idx],
                                min_qtys[idx],
                                min_costs[idx],
                                c_mults[idx],
                                flc[0][4],
                                flc[0][3],
                                flc[0][0],
                                flc[0][9],
                                flc[0][10],
                                wallet_exposure_limit_long,
                                auto_unstuck_ema_dist,
                                auto_unstuck_wallet_exposure_threshold,
                                auto_unstuck_on_timer,
                            )
                            new_open_orders_entry_long.append((idx, [entry]))
                    open_orders_entry_long = new_open_orders_entry_long
        if k % 60 == 0:
            equity = balance + calc_pnl_sum(
                positions_long, positions_short, hlcs[k, :, 1], hlcs[k, :, 0], c_mults
            )
            stats.append(
                (
                    k,  # minute
                    positions_long.copy(),
                    positions_short.copy(),
                    hlcs[k, :, 2],
                    balance,
                    equity,
                )
            )

    return fills, stats


@njit
def calc_unstuck_order(
    c_mults,
    qty_steps,
    price_steps,
    min_costs,
    min_qtys,
    flc,
    wallet_exposure_limit_long,
    wallet_exposure_limit_short,
    balance,
    drop_since_peak_abs,
    is_stuck_long,
    is_stuck_short,
    positions_long,
    positions_short,
    emas_long,
    emas_short,
    hlcs_k,
) -> (int, int, (float, float, str)):
    # returns (pside: int, idx: int, (qty: float, price: float, type: str))
    if not (is_stuck_long or is_stuck_short):
        return (0, 0, (0.0, 0.0, ""))
    inverse = False
    pprice_diffs = []
    for idx in is_stuck_long:
        pprice_diff = calc_pprice_diff_int(0, positions_long[idx][1], hlcs_k[idx][2])
        pprice_diffs.append((pprice_diff, 0, idx))
    for idx in is_stuck_short:
        pprice_diff = calc_pprice_diff_int(1, positions_short[idx][1], hlcs_k[idx][2])
        pprice_diffs.append((pprice_diff, 1, idx))
    pprice_diff, pside, idx = sorted(pprice_diffs)[0]
    AU_allowance = calc_AU_allowance(
        np.array([0.0]),
        balance,
        loss_allowance_pct=flc[pside][14],
        drop_since_peak_abs=drop_since_peak_abs,
    )
    if AU_allowance <= 0.0:
        return (0, 0, (0.0, 0.0, ""))
    if pside == 0:
        close_price = max(
            hlcs_k[idx][2], round_up(max(emas_long[idx]) * (1.0 + flc[0][13]), price_steps[idx])
        )
        close_qty = -min(
            positions_long[idx][0],
            max(
                calc_min_entry_qty(
                    close_price, inverse, c_mults[idx], qty_steps[idx], min_qtys[idx], min_costs[idx]
                ),
                cost_to_qty(
                    balance * wallet_exposure_limit_long * flc[0][12],
                    close_price,
                    inverse,
                    c_mults[idx],
                ),
            ),
        )
        close_type = "unstuck_close_long"
    else:
        close_price = min(
            hlcs_k[idx][2], round_dn(min(emas_short[idx]) * (1.0 - flc[1][13]), price_steps[idx])
        )
        close_qty = 0.01
        close_type = "unstuck_close_short"

    return (pside, idx, (close_qty, close_price, close_type))
