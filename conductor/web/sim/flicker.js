/* flicker.js — the e-paper refresh flicker simulator, extracted from
 * conductor/web/index.html (as of commit 243bbb1), lines 706-852:
 * JITTER_MAX/REFRESH_TINT/REFRESH_PHASE_A/REFRESH_PALETTE/REFRESH_PHASE_C
 * (711-716), hash32 (721-726), hexOfCode/hexToRgb/TINT_STEPS/tintedHex
 * (727-748), refreshModelFor (749-768), refreshPhaseColor (769-788),
 * applySweptColors/applyFlatColors (789-824), wornAt (825-852).
 *
 * Kept verbatim except the parameterisation plan_designer_sim.md §2.3 names for
 * wornAt: `opts {flicker, cuesOf, palette, fw}`. The operator page reads the show's
 * cues and palette off two module-level globals (`cuesOf(item)`, itself reading
 * `state.show.cues`, and `state.palette` via hexOfCode/rgb); the designer has no
 * such globals, so both travel down as parameters from wornAt's own opts, threaded
 * through every helper that used to read them off `state` directly (maxRankOf's
 * cache, hexOfCode, tintedHex, refreshPhaseColor, applySweptColors/applyFlatColors).
 * `maxRankCache`/`refreshModelCache` stay as this module's own private caches
 * (SIM.flicker.clearCaches() replaces the operator page's per-refresh() clearing).
 *
 * 2026-11-19: a scale's refresh plays the selected panel firmware profile's
 * MEASURED curve for its target code (sim/panelfw.js, loaded before this
 * file; made from the reference video by tools/make_panelfw.py) instead of
 * the old generic flicker - REFRESH_TINT/REFRESH_PHASE_A/REFRESH_PALETTE/
 * REFRESH_PHASE_C/TINT_STEPS and tintedHex went with it (user, 2026-11-19).
 */
(function () {
  "use strict";

  const JITTER_MAX = 0.35;
  // On a swept cue the jitter is capped at this fraction of the sweep's own step
  // between consecutive ranks (adversarial review, 2026-09-26): the hardware has
  // no jitter at all - scales whose delay table gives them equal frames start in
  // the same 10 ms frame - and at a 3 s span over ~21 ranks the step is 0.14 s,
  // so an uncapped 0.35 s scrambled about two and a half rows of the preview and
  // made a real sweep look messier than it is. The full JITTER_MAX stays for a
  // flat refresh, where it models the panel-to-panel variance we did film.
  const SWEEP_JITTER_FRAC = 0.3;

  const maxRankCache = new Map();
  const refreshModelCache = new Map();
  const leadHexCache = new Map();

  function clearCaches() {
    maxRankCache.clear();
    refreshModelCache.clear();
    leadHexCache.clear();
  }

  // model.js's hash32 (2026-10-22): it moved there when the sweep's edge
  // ranks needed it, so the simulator keeps exactly one implementation.
  const hash32 = SIM.sequence.hash32;
  function hexOfCode(code, palette) {
    const [r, g, b] = palette[code].rgb;
    return "#" + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, "0")).join("");
  }
  function hexToRgb(hex) { return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)]; }
  // The lead-in on a scale that did not start white (the recording did): the
  // previous colour `fromHex` with the measured change from white added -
  // the navy of a level-6 lead-in, the yellow of a level-1 one - and, over
  // the first cross_ticks of the shake (step 1..cross_ticks-1), blended into
  // the measured colour, which is used as is from there on: the shake resets
  // the panel, whatever it showed before (assumption, 2026-11-19 - the video
  // only has refreshes from white). Memoised per from+measured+step. The
  // SAME CODE as index.html's leadHex.
  function leadHex(fromHex, measuredHex, step, fw) {
    const key = fromHex + "|" + measuredHex + "|" + step;
    let out = leadHexCache.get(key);
    if (out === undefined) {
      const from = hexToRgb(fromHex), measured = hexToRgb(measuredHex), white = hexToRgb(fw.white);
      const w = step / fw.cross_ticks;
      const mixed = measured.map((v, i) => {
        const lead = Math.max(0, Math.min(255, from[i] + v - white[i]));
        return Math.round(lead * (1 - w) + v * w);
      });
      out = "#" + mixed.map(v => v.toString(16).padStart(2, "0")).join("");
      leadHexCache.set(key, out);
    }
    return out;
  }
  // The index of the last key at or before tick (keys sorted by tick, the
  // first at tick 0). The SAME CODE as index.html's keyAt.
  function keyAt(keys, tick) {
    let lo = 0, hi = keys.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (keys[mid][0] <= tick) lo = mid; else hi = mid - 1;
    }
    return lo;
  }
  function maxRankOf(item, sequence, ranks) {
    const key = item.item + "|" + sequence;     // "<sequence>#<rank_set>" when the edge is not straight
    let m = maxRankCache.get(key);
    if (m === undefined) { m = Math.max(...ranks, 0); maxRankCache.set(key, m); }
    return m;
  }
  // A sweep with a wavy/frayed edge (2026-10-22) plays its own ranks,
  // item.sweep_ranks[sweep.rank_set], which state.js computes once per
  // distinct edge, as server.py does for the Conductor page; a straight one
  // (rank_set "") keeps item.sequences[sequence] as before. The SAME CODE as
  // index.html's refreshModelFor (test_the_flicker_timing_is_the_same_code_
  // on_both_screens). The rank set is part of both cache keys, so an edited
  // edge is never drawn from the old one; rebuild() clears both on every
  // edit anyway.
  function refreshModelFor(item, cue, sweep) {
    const rankSet = typeof sweep.rank_set === "string" ? sweep.rank_set : "";
    const edged = rankSet ? item.sweep_ranks?.[rankSet] : undefined;
    const key = item.item + "|" + cue.id + "|" + rankSet;
    let m = refreshModelCache.get(key);
    if (m) return m;
    const ranks = edged || item.sequences?.[sweep.sequence];
    const maxRank = ranks ? maxRankOf(item, edged ? sweep.sequence + "#" + rankSet : sweep.sequence, ranks) : 0;
    const n = item.map.scales.length;
    const delay = new Float64Array(n), jitter = new Float64Array(n);
    const swept = !!ranks && maxRank > 0 && sweep.span_s > 0;
    // A swept scale's jitter may not eat its own step (see SWEEP_JITTER_FRAC).
    const cap = swept ? Math.min(JITTER_MAX, SWEEP_JITTER_FRAC * sweep.span_s / maxRank)
                      : JITTER_MAX;
    for (let i = 0; i < n; i++) {
      delay[i] = ranks && maxRank > 0 ? ranks[i] * sweep.span_s / maxRank : 0;
      jitter[i] = (hash32(i, 0xa5a5) % 1000) / 1000 * cap;
    }
    m = { delay, jitter };
    refreshModelCache.set(key, m);
    return m;
  }
  // One scale's look `tick` waveform ticks into its own refresh (2026-11-19):
  // the measured curve of the target code in the panel firmware profile fw
  // (a colour per key, held to the next), the palette colour from the
  // curve's end (drive_end) on. `prev` is what the scale showed before (a
  // code, a "#rrggbb", or undefined for the bare garment - taken as white,
  // like the recording): it shapes the lead-in and the start of the shake
  // only (leadHex). The SAME CODE as index.html's refreshPhaseColor.
  function refreshPhaseColor(tick, targetCode, prev, palette, fw) {
    const curve = fw.curves[targetCode];
    if (tick >= curve.end) return targetCode;
    const measured = curve.keys[keyAt(curve.keys, tick)][1];
    const cross = tick - fw.lead_in_ticks;
    if (cross >= fw.cross_ticks) return measured;
    const from = prev === undefined ? fw.white : typeof prev === "string" ? prev : hexOfCode(prev, palette);
    return leadHex(from, measured, cross < 0 ? 0 : Math.floor(cross), fw);
  }
  const sweepOf = cue => cue.sweep || { sequence: "natural", span_s: 0, source: "design" };
  function refreshOfFor(cue, showRefreshS) {
    if (cue.refresh !== undefined) return { value: cue.refresh, source: cue.refresh_source || (cue.refresh_s != null ? "cue" : "show") };
    return cue.refresh_s != null ? { value: cue.refresh_s, source: "cue" } : { value: showRefreshS, source: "show" };
  }
  // ONE SCALE's own repaint - what the flicker below is phased over, and NOT
  // the cue's refresh. Since 2026-09-26 a refresh is the whole change, the
  // sweep INCLUDED (8 s = a 7 s panel repaint with a 1 s sweep inside it - a
  // 7.3 s one since 2026-11-19, so a 1 s sweep now completes at 8.3 s), so
  // phasing a scale over the refresh made the last scale of a swept cue still
  // mid-flicker at Complete, where wornAt() drops the whole design in at once
  // and the preview snapped. A scale takes one PHYSICAL repaint, capped at the
  // cue's own refresh exactly as conductor/timeline.py's panel_repaint_of()
  // does (a show that declares a 1 s refresh must not be given a 7 s one), so
  // the last scale of a swept cue now settles at delay + repaint = Complete,
  // and an all-at-once cue settles at the repaint, one second inside its own
  // 8 s budget - which is what the hardware does.
  function panelRepaintFor(cue, showRefreshS) {
    return Math.min(SIM.timeline.PANEL_REPAINT_S,
                    refreshOfFor(cue, showRefreshS).value);
  }
  // A scale's jitter is a LATE START, so it has to come out of that scale's
  // own repaint or the scale finishes after the cue does: at Complete
  // wornAt() drops the finished design in wholesale, and a scale still a
  // third of a second from settling snapped instead of arriving (review,
  // 2026-09-26 - the first probe for this ran with flicker:false, which has
  // no jitter, and missed it). Never more than half the repaint either, so a
  // compressed show (a 0.3 s refresh on the bench) keeps a visible flicker.
  function startJitter(raw, repaint) { return Math.min(raw, repaint / 2); }
  function applySweptColors(item, cue, design, t, out, palette, showRefreshS, fw) {
    const sweep = sweepOf(cue);
    // panelRepaintFor(), never a hard-coded number (adversarial review,
    // 2026-09-25: this used to fall back to a bare "7" - a silent duplicate
    // of timeline.js's own default that would quietly drift the moment that
    // constant ever changed, and wrong whenever the show's own refresh isn't
    // 7.0s in the first place, e.g. after Default refresh time is edited on
    // the Timeline toolbar).
    const repaint = panelRepaintFor(cue, showRefreshS);
    // The waveform as measured: 662 ticks x 11 ms = 7.28 s, inside the
    // model's repaint (PANEL_REPAINT_S, 7.3 s since 2026-11-19), so it plays
    // at true speed; never past the repaint, though - a scale still drawing
    // at Complete would snap there - so a show that declares a shorter
    // refresh (a legacy 7 s one, a bench run) squeezes it into that.
    const wave = Math.min(repaint, fw.wave_ticks * fw.tick_s);
    const model = refreshModelFor(item, cue, sweep);
    item.map.scales.forEach((s, n) => {
      const key = `${s[0]}|${s[1]}|${s[2]}`;
      const targetCode = design.colors[key];
      if (targetCode === undefined) return;
      const jitter = startJitter(model.jitter[n], wave);
      const tau = t - (cue.sent + model.delay[n] + jitter);
      if (tau < 0) return;
      // The jitter is a late start, so a scale plays its waveform a little
      // faster to end at delay + wave like every other: a code whose drive
      // ends at 662 lands at the same instant on every scale of a flat
      // refresh, as FW_261119F does on the garment (2026-11-19).
      const own = wave - jitter;
      // Done at the same sum applyFlatColors uses, not tau >= own: one ulp short
      // of it and the last measured key would still show at the very end.
      out[key] = t >= cue.sent + model.delay[n] + wave ? targetCode : refreshPhaseColor(tau / own * fw.wave_ticks, targetCode, out[key], palette, fw);
    });
  }
  function applyFlatColors(item, cue, design, t, out, showRefreshS) {
    const sweep = sweepOf(cue);
    const repaint = panelRepaintFor(cue, showRefreshS);
    const model = refreshModelFor(item, cue, sweep);
    item.map.scales.forEach((s, n) => {
      const key = `${s[0]}|${s[1]}|${s[2]}`;
      const targetCode = design.colors[key];
      if (targetCode === undefined) return;
      if (t >= cue.sent + model.delay[n] + repaint) out[key] = targetCode;
    });
  }
  const designLabel = d => d.label || d.name;
  // opts: {flicker (default true), cuesOf(item)->cues, palette (already the
  // selected profile's - SIM.panelFw.paletteOf), fw (the panel firmware
  // profile's id; absent = panelfw.js's default), refreshS
  // (state.show.refresh_s - the show's own default, used only for a cue
  // that carries no override of its own; state.show.cues[] normally already
  // has a computed .refresh by the time it reaches here, so this is a
  // fallback path, not the common one)}
  function wornAt(item, t, opts) {
    const flicker = !opts || opts.flicker !== false;
    const cues = (opts && opts.cuesOf) ? opts.cuesOf(item) : [];
    const palette = opts && opts.palette;
    const showRefreshS = opts && opts.refreshS;
    const fw = SIM.panelFw.profile(opts && opts.fw);
    let colors = {}, shifts = null, label = "(as before the show)", changing = false;
    for (const cue of cues) {
      const design = item.designs.find(d => d.name === cue.design);
      if (cue.sent <= t && t < cue.complete) changing = true;
      if (!design) continue;
      if (cue.complete > t) {
        if (cue.sent <= t) {
          if (flicker) applySweptColors(item, cue, design, t, colors, palette, showRefreshS, fw);
          else applyFlatColors(item, cue, design, t, colors, showRefreshS);
        }
        continue;
      }
      colors = cue.partial ? { ...colors, ...design.colors } : { ...design.colors };
      shifts = design.shifts; label = designLabel(design) + (cue.partial ? " (partial)" : "");
    }
    return { colors, shifts, label, changing };
  }

  globalThis.SIM = Object.assign(globalThis.SIM || {}, {
    flicker: {
      JITTER_MAX,
      hash32, leadHex, keyAt, refreshPhaseColor, refreshModelFor, refreshOfFor, panelRepaintFor,
      startJitter, wornAt, clearCaches,
    },
  });
})();
