// WPBrücke · for WordPress: the Desktop half. One page (/wpbruecke) with six views (Home, Review desk,
// SEO, Build run, Connections, Settings), a sidebar row "WPBrücke · N" (N = desk items waiting for you;
// the shared sidebar-row module, hidden or moved in the settings), three palette commands, a settings
// page and the transcript directive ::wp-draft{site="..." id="..."}.
//
// Everything the page shows comes from this plugin's own backend routes (dashboard/plugin_api.py) through
// ctx.rest, polled with React Query; the page never holds a WordPress password. A site is connected in
// Hermes' own settings form (Site N · address, user, application password); Connections guides that,
// checks the connection and records the access level on the person's Confirm. Opening the settings form
// unmounts this page, so every connection state is derived from GET /groups and GET /overview on each
// mount, never from component state. Publish, Schedule and Apply act directly when the site's level is
// Publish (`can_publish`); below it they open WordPress, where a person's own login does the click.
// "New blog draft" and "Ask for changes" fill the chat composer and never send.
//
// Plain ESM, loaded uncompiled: jsx() calls, imports only @hermes/plugin-sdk, react and
// react/jsx-runtime. Theme variables only; one scoped stylesheet with wpb- classes.

import {
  atom,
  Codicon,
  host,
  Input,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  Switch,
  TRANSCRIPT_DIRECTIVE_AREA,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
// Optional SDK names are read from the namespace: Desktop's shim exports only the names it has, and a
// named import of one it lacks fails this whole file at load.
import * as sdkNs from '@hermes/plugin-sdk'
import { Component, useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'wpbruecke'
const PATH = '/wpbruecke'
const TAG = '[' + ID + ']'
const STYLE_ID = 'wpb-style'
const POLL_MS = 30000
const NAV_POLL_MS = 60000
const JOB_POLL_MS = 2000
const SETTINGS_HREF = '/settings?tab=plugins&agent=wpbruecke'
const SITE_RE = /^[a-z0-9][a-z0-9-]{0,63}$/
const ITEM_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/
const VIEWS = ['home', 'desk', 'seo', 'build', 'connect', 'settings', 'site']
const LIMIT_LINE = 'Only the WordPress role holds against everything; anything with shell access on the Hermes host can read the stored password.'
// The connector version this plugin ships (connector_zip.CONNECTOR_VERSION; a test holds them equal).
const CONNECTOR_NEEDED = '0.5.0'
const CONNECTOR_CLI = 'hermes wpbruecke connector-zip --out PATH'

let pluginCtx = null
// The sidebar row (the shared module below), kept here so the settings page can draw its Sidebar section.
let sidebarRow = null

function log(step, value) {
  try {
    console.info(TAG, step, value === undefined ? '' : value)
  } catch {
    /* no console */
  }
}

function el(type, props, ...children) {
  const { key, ...rest } = props || {}
  const kids = children.flat().filter(c => c !== undefined && c !== null && c !== false)
  if (kids.length === 0) return jsx(type, rest, key)
  if (kids.length === 1) return jsx(type, { ...rest, children: kids[0] }, key)
  return jsxs(type, { ...rest, children: kids }, key)
}

const cx = (...parts) => parts.filter(Boolean).join(' ')

// ── styles: one scoped sheet, wpb- classes on the app's theme variables ──────────────────────────

const CSS = `
.wpb-scope{--wpb-fg:var(--ui-text-primary);--wpb-muted:var(--ui-text-secondary);--wpb-faint:var(--ui-text-tertiary);
--wpb-line:var(--ui-stroke-tertiary);--wpb-line2:var(--ui-stroke-secondary);--wpb-panel:var(--ui-bg-card,transparent);
--wpb-acc:var(--ui-accent);--wpb-ok:var(--ui-green);--wpb-warn:var(--ui-yellow);--wpb-bad:var(--ui-red);
--wpb-sub:color-mix(in srgb,var(--wpb-fg) 4%,transparent);--wpb-sub2:color-mix(in srgb,var(--wpb-fg) 8%,transparent);
--wpb-acc-soft:color-mix(in srgb,var(--wpb-acc) 12%,transparent);
--wpb-mono:var(--font-mono,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace);--wpb-r:6px;
color:var(--wpb-fg);font-size:13px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wpb-scope *{box-sizing:border-box}
.wpb{height:100%;overflow-y:auto;overflow-x:hidden;container-type:inline-size;container-name:wpb}
.wpb-in{padding:18px 22px 48px;max-width:1440px}
.wpb-scope h1,.wpb-scope h2,.wpb-scope h3{margin:0;text-wrap:balance}
.wpb-scope p{margin:0;text-wrap:pretty}
:where(.wpb-scope) button{font:inherit;color:inherit}
.wpb-h1{font-size:17px;font-weight:650;letter-spacing:-.01em}
.wpb-h2{font-size:15px;font-weight:620}
.wpb-h3{font-size:13px;font-weight:620}
.wpb-muted{color:var(--wpb-muted)} .wpb-faint{color:var(--wpb-faint)} .wpb-small{font-size:12px} .wpb-xs{font-size:11px}
.wpb-mono{font-family:var(--wpb-mono);font-variant-numeric:tabular-nums}
.wpb-top{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;margin-bottom:10px}
.wpb-top .wpb-sp{flex:1}
.wpb-crumb{display:flex;gap:6px;align-items:baseline;flex-wrap:wrap;margin-bottom:12px;font-size:12px}
.wpb-link{background:none;border:0;padding:0;color:var(--wpb-acc);cursor:pointer;text-decoration:none}
.wpb-link:hover{text-decoration:underline}
.wpb-tabs{display:flex;gap:2px;border-bottom:1px solid var(--wpb-line);overflow-x:auto;scrollbar-width:none;margin-bottom:16px}
.wpb-tab{background:none;border:0;border-bottom:2px solid transparent;padding:8px 12px 9px;white-space:nowrap;color:var(--wpb-muted);display:flex;gap:7px;align-items:baseline;margin-bottom:-1px;cursor:pointer}
.wpb-tab:hover{color:var(--wpb-fg)}
.wpb-tab[aria-selected=true]{color:var(--wpb-fg);border-bottom-color:var(--wpb-acc);font-weight:600}
.wpb-tab .wpb-n{font-family:var(--wpb-mono);font-size:11px;color:var(--wpb-acc)}
.wpb-lede{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline;margin-bottom:14px}
.wpb-lede p{color:var(--wpb-muted);max-width:84ch}
.wpb-chip{border:1px solid var(--wpb-line2);background:none;border-radius:999px;padding:3px 10px;font-size:12px;display:inline-flex;gap:6px;align-items:center;white-space:nowrap;cursor:pointer}
.wpb-chip .wpb-n{font-family:var(--wpb-mono);color:var(--wpb-muted);font-size:11px}
.wpb-chip:hover{background:var(--wpb-sub)}
.wpb-chip[aria-pressed=true]{background:var(--wpb-fg);color:var(--ui-bg-editor,var(--wpb-panel));border-color:var(--wpb-fg)}
.wpb-chip[aria-pressed=true] .wpb-n{color:inherit;opacity:.7}
.wpb-btn{border:1px solid var(--wpb-line2);background:var(--wpb-panel);border-radius:var(--wpb-r);padding:5px 11px;font-size:12px;white-space:nowrap;display:inline-flex;align-items:center;gap:6px;cursor:pointer;line-height:18px}
.wpb-btn:hover{background:var(--wpb-sub2)} .wpb-btn:active{transform:translateY(1px)}
.wpb-btn-primary{background:var(--dt-primary-solid,var(--wpb-acc));border-color:var(--dt-primary-solid,var(--wpb-acc));color:var(--dt-primary-solid-foreground,var(--dt-primary-foreground))}
.wpb-btn-primary:hover{background:color-mix(in srgb,var(--dt-primary-solid,var(--wpb-acc)) 88%,var(--wpb-fg))}
.wpb-btn-ghost{border-color:transparent;background:none;color:var(--wpb-muted)}
.wpb-btn-ghost:hover{color:var(--wpb-fg)}
.wpb-btn[disabled]{opacity:.45;cursor:not-allowed;transform:none}
.wpb-scope button:focus-visible,.wpb-scope select:focus-visible,.wpb-scope textarea:focus-visible{outline:2px solid var(--wpb-acc);outline-offset:1px}
.wpb-badge{font-size:11px;border-radius:999px;padding:1px 8px;border:1px solid var(--wpb-line2);white-space:nowrap;display:inline-flex;gap:5px;align-items:center;line-height:17px}
.wpb-badge-good{border-color:color-mix(in srgb,var(--wpb-ok) 45%,transparent);color:var(--wpb-ok)}
.wpb-badge-warn{border-color:color-mix(in srgb,var(--wpb-warn) 50%,transparent);color:var(--wpb-warn)}
.wpb-badge-bad{border-color:color-mix(in srgb,var(--wpb-bad) 50%,transparent);color:var(--wpb-bad)}
.wpb-dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none;background:var(--wpb-faint)}
.wpb-dot-ok{background:var(--wpb-ok)} .wpb-dot-warn{background:var(--wpb-warn)} .wpb-dot-bad{background:var(--wpb-bad)}
.wpb-panel{border:1px solid var(--wpb-line);border-radius:var(--wpb-r);background:var(--wpb-panel);min-width:0}
.wpb-who{font-size:11px;font-family:var(--wpb-mono);padding:0 7px;border-radius:999px;line-height:18px;white-space:nowrap;border:1px solid var(--wpb-line2)}
.wpb-who-hermes{color:var(--wpb-acc);border-color:color-mix(in srgb,var(--wpb-acc) 40%,transparent)}
.wpb-who-you{background:var(--wpb-sub2);border-color:transparent}
.wpb-who-client{color:var(--wpb-warn);border-color:color-mix(in srgb,var(--wpb-warn) 45%,transparent)}
.wpb-proofs{display:flex;flex-wrap:wrap;gap:6px}
.wpb-proof{display:inline-flex;gap:6px;align-items:center;font-size:12px;border-radius:var(--wpb-r);padding:3px 8px;border:1px solid var(--wpb-line2);line-height:18px;max-width:100%}
.wpb-proof .wpb-s{font-family:var(--wpb-mono);font-size:10px;text-transform:uppercase;letter-spacing:.04em;flex:none}
.wpb-proof-match{border-color:color-mix(in srgb,var(--wpb-ok) 40%,transparent)} .wpb-proof-match .wpb-s{color:var(--wpb-ok)}
.wpb-proof-differs{border-color:color-mix(in srgb,var(--wpb-warn) 50%,transparent);background:color-mix(in srgb,var(--wpb-warn) 7%,transparent)} .wpb-proof-differs .wpb-s{color:var(--wpb-warn)}
.wpb-proof-not_proven{border-style:dashed;color:var(--wpb-muted)} .wpb-proof-not_proven .wpb-s{color:var(--wpb-muted)}
.wpb-seg{display:inline-flex;border:1px solid var(--wpb-line2);border-radius:var(--wpb-r);overflow:hidden;flex-wrap:wrap;max-width:100%}
.wpb-seg button{border:0;background:none;padding:4px 10px;font-size:12px;color:var(--wpb-muted);cursor:pointer}
.wpb-seg button+button{border-left:1px solid var(--wpb-line2)}
.wpb-seg button[aria-pressed=true]{background:var(--wpb-sub2);color:var(--wpb-fg);font-weight:600}
.wpb-seg button[disabled]{opacity:.6;cursor:not-allowed}
.wpb-switch{display:inline-flex;align-items:center;gap:8px;background:none;border:0;padding:0;text-align:left;cursor:pointer}
.wpb-switch[disabled]{cursor:default}
.wpb-switch .wpb-tr{width:30px;height:17px;border-radius:9px;background:var(--wpb-sub2);position:relative;border:1px solid var(--wpb-line2);flex:none;transition:background .15s}
.wpb-switch .wpb-tr::after{content:"";position:absolute;width:13px;height:13px;border-radius:50%;background:var(--wpb-muted);top:1px;left:1px;transition:left .15s,background .15s}
.wpb-switch[aria-checked=true] .wpb-tr{background:color-mix(in srgb,var(--wpb-acc) 30%,transparent);border-color:var(--wpb-acc)}
.wpb-switch[aria-checked=true] .wpb-tr::after{left:14px;background:var(--wpb-acc)}
.wpb-grid2{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px;align-items:start}
.wpb-stack{display:flex;flex-direction:column;gap:12px;min-width:0}
.wpb-strip{display:flex;flex-wrap:wrap;border:1px solid var(--wpb-line);border-radius:var(--wpb-r);margin-bottom:12px;background:var(--wpb-panel)}
.wpb-strip>div{padding:8px 14px;border-right:1px solid var(--wpb-line);min-width:118px;flex:1}
.wpb-strip>div:last-child{border-right:0}
.wpb-strip b{display:block;font-family:var(--wpb-mono);font-size:15px;font-weight:600}
.wpb-strip span{font-size:11px;color:var(--wpb-muted)}
.wpb-filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:12px}
.wpb-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,290px),1fr));gap:12px}
.wpb-site{display:flex;flex-direction:column}
.wpb-site-h{display:flex;gap:10px;padding:12px 12px 10px;align-items:flex-start}
.wpb-mark{width:34px;height:34px;border-radius:var(--wpb-r);display:grid;place-items:center;font:600 12px/1 var(--wpb-mono);flex:none;background:var(--wpb-sub2)}
.wpb-site-h .wpb-t{min-width:0;flex:1} .wpb-site-h .wpb-t b{font-weight:600;display:block;overflow-wrap:anywhere}
.wpb-meta{display:flex;flex-wrap:wrap;gap:4px 6px;margin-top:5px;align-items:center}
.wpb-health{display:inline-flex;gap:6px;align-items:center;font-size:11px;color:var(--wpb-muted);white-space:nowrap}
.wpb-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-top:1px solid var(--wpb-line);border-bottom:1px solid var(--wpb-line)}
.wpb-stats>div{padding:7px 12px;min-width:0} .wpb-stats>div+div{border-left:1px solid var(--wpb-line)}
.wpb-stats b{font-family:var(--wpb-mono);font-size:14px;font-weight:600;display:block} .wpb-stats b.wpb-hot{color:var(--wpb-acc)}
.wpb-stats span{font-size:11px;color:var(--wpb-muted)}
.wpb-site-b{padding:9px 12px;display:flex;flex-direction:column;gap:7px;flex:1}
.wpb-access{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;background:var(--wpb-sub);border-radius:var(--wpb-r);padding:6px 8px;font-size:12px}
.wpb-site-f{display:flex;flex-wrap:wrap;gap:6px;padding:0 12px 12px}
.wpb-side h2{display:flex;justify-content:space-between;align-items:baseline;padding:12px 14px 8px;gap:8px}
.wpb-need{display:flex;gap:10px;padding:10px 14px;border:0;border-top:1px solid var(--wpb-line);align-items:flex-start;width:100%;background:none;text-align:left;cursor:pointer}
.wpb-need:hover{background:var(--wpb-sub)}
.wpb-need .wpb-w{flex:1;min-width:0} .wpb-need .wpb-w b{font-weight:550;display:block} .wpb-need .wpb-w span{font-size:12px;color:var(--wpb-muted);display:block}
.wpb-need .wpb-when{font-family:var(--wpb-mono);font-size:11px;white-space:nowrap;display:flex;gap:6px;align-items:baseline;color:var(--wpb-muted)}
.wpb-need .wpb-when::after{content:"\\203A";font-size:14px;line-height:1}
.wpb-need .wpb-when.wpb-urgent{color:var(--wpb-bad)}
.wpb-side-note{padding:10px 14px;border-top:1px solid var(--wpb-line);font-size:12px;color:var(--wpb-muted)}
.wpb-hero{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;justify-content:space-between;padding:14px 16px;margin-bottom:12px}
.wpb-hero .wpb-id{display:flex;gap:12px;align-items:center;min-width:0}
.wpb-hero .wpb-id b{font-size:16px;font-weight:650}
.wpb-list h2{padding:12px 14px 8px}
.wpb-row{display:flex;gap:10px;justify-content:space-between;align-items:center;padding:9px 14px;border-top:1px solid var(--wpb-line);flex-wrap:wrap}
.wpb-row>span:first-child{min-width:0;flex:1 1 220px}
.wpb-row b{font-weight:560}
.wpb-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.wpb-desk{display:grid;grid-template-columns:330px minmax(0,1fr);gap:16px;align-items:start}
.wpb-qhead{padding:10px 12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--wpb-line)}
.wpb-qitem{display:block;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--wpb-line);padding:10px 12px;position:relative;cursor:pointer}
.wpb-qitem:last-child{border-bottom:0} .wpb-qitem:hover{background:var(--wpb-sub)}
.wpb-qitem[aria-selected=true]{background:var(--wpb-acc-soft)}
.wpb-qitem[aria-selected=true]::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--wpb-acc)}
.wpb-qitem.wpb-done{opacity:.75}
.wpb-qsep{padding:6px 12px;font-size:11px;color:var(--wpb-muted);border-bottom:1px solid var(--wpb-line);background:var(--wpb-sub)}
.wpb-v-match{color:var(--wpb-ok)} .wpb-v-differs{color:var(--wpb-warn)} .wpb-v-not_proven{color:var(--wpb-muted)}
.wpb-qitem .wpb-r1{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.wpb-qitem .wpb-ttl{font-weight:560;min-width:0;overflow-wrap:anywhere}
.wpb-qitem .wpb-r2{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:11.5px;color:var(--wpb-muted);margin-top:3px}
.wpb-type{font-family:var(--wpb-mono);font-size:10.5px;color:var(--wpb-muted);white-space:nowrap}
.wpb-st{font-family:var(--wpb-mono);font-size:11px;white-space:nowrap} .wpb-st-done{color:var(--wpb-ok)} .wpb-st-wait{color:var(--wpb-warn)}
.wpb-detail{padding:14px 16px}
.wpb-meta2{display:flex;flex-wrap:wrap;gap:4px 12px;color:var(--wpb-muted);font-size:12px;margin:3px 0 12px}
.wpb-tools{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
.wpb-diffpct{font-family:var(--wpb-mono);font-size:12px} .wpb-diffpct b{font-size:15px}
.wpb-shots{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;margin-bottom:12px}
.wpb-shots.wpb-single{grid-template-columns:minmax(0,1fr)}
.wpb-shot{border:1px solid var(--wpb-line);border-radius:var(--wpb-r);padding:8px;background:var(--wpb-sub);min-width:0}
.wpb-cap{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--wpb-muted);margin-bottom:6px;font-family:var(--wpb-mono)}
.wpb-stage{display:flex;justify-content:center;align-items:flex-start;max-height:420px;overflow:hidden}
.wpb-frame{width:100%;border:1px solid var(--wpb-line);border-radius:4px;overflow:hidden;transition:width .2s;background:var(--wpb-panel)}
.wpb-frame.wpb-tablet{width:66%} .wpb-frame.wpb-mobile{width:40%;min-width:110px}
.wpb-shots.wpb-single .wpb-frame{max-width:62%}
.wpb-frame img{display:block;width:100%;height:auto}
.wpb-noshot{padding:28px 12px;text-align:center;font-size:12px;color:var(--wpb-muted)}
.wpb-pubbox{margin-top:18px;padding:12px;border:1px solid var(--wpb-line);border-radius:var(--wpb-r);background:var(--wpb-sub);display:flex;flex-direction:column;gap:10px}
.wpb-pubrow{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.wpb-notice{border:1px solid color-mix(in srgb,var(--wpb-warn) 50%,transparent);background:color-mix(in srgb,var(--wpb-warn) 8%,transparent);border-radius:var(--wpb-r);padding:8px 10px;font-size:12px;display:flex;flex-direction:column;gap:6px}
.wpb-notice.wpb-info{border-color:var(--wpb-line2);background:var(--wpb-sub)}
.wpb-notice.wpb-bad{border-color:color-mix(in srgb,var(--wpb-bad) 50%,transparent);background:color-mix(in srgb,var(--wpb-bad) 7%,transparent)}
.wpb-textarea,.wpb-select{font:inherit;font-size:12px;color:var(--wpb-fg);background:var(--wpb-panel);border:1px solid var(--wpb-line2);border-radius:var(--wpb-r);padding:5px 8px;max-width:100%}
.wpb-textarea{width:100%;min-height:64px;resize:vertical}
.wpb-select option{color:initial}
.wpb-checks{display:flex;flex-direction:column}
.wpb-check{display:grid;grid-template-columns:92px minmax(0,1fr) auto;gap:10px;align-items:start;padding:9px 14px;border-top:1px solid var(--wpb-line)}
.wpb-check .wpb-s{font-family:var(--wpb-mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;padding-top:2px}
.wpb-s-match{color:var(--wpb-ok)} .wpb-s-differs{color:var(--wpb-warn)} .wpb-s-not_proven{color:var(--wpb-muted)}
.wpb-check b{font-weight:560;display:block}
.wpb-check .wpb-d{font-size:12px;color:var(--wpb-muted);display:block;overflow-wrap:anywhere}
.wpb-score{font-family:var(--wpb-mono);font-size:26px;font-weight:650}
.wpb-phead{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;padding:12px 14px 8px}
.wpb-phead .wpb-h3{margin-right:auto}
.wpb-step{display:grid;grid-template-columns:30px minmax(0,1fr);gap:12px;padding-bottom:12px;position:relative}
.wpb-step::before{content:"";position:absolute;left:14px;top:30px;bottom:0;width:1px;background:var(--wpb-line)}
.wpb-step:last-child::before{display:none}
.wpb-num{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font:600 12px var(--wpb-mono);border:1px solid var(--wpb-line2);background:var(--ui-bg-editor,var(--wpb-panel));position:relative;z-index:1}
.wpb-step-done .wpb-num{background:var(--wpb-fg);color:var(--ui-bg-editor,var(--wpb-panel));border-color:var(--wpb-fg)}
.wpb-step-running .wpb-num,.wpb-step-waiting .wpb-num{border:2px solid var(--wpb-acc);color:var(--wpb-acc)}
.wpb-sbody{border:1px solid var(--wpb-line);border-radius:var(--wpb-r);background:var(--wpb-panel);min-width:0}
.wpb-shead{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;padding:9px 12px;width:100%;background:none;border:0;text-align:left;cursor:pointer}
.wpb-shead .wpb-h3{margin-right:auto}
.wpb-scont{border-top:1px solid var(--wpb-line);padding:10px 12px;font-size:12px;display:flex;flex-direction:column;gap:8px}
.wpb-gate{display:flex;gap:8px;font-size:12px;padding:7px 10px;border-radius:var(--wpb-r);background:var(--wpb-sub);flex-wrap:wrap;align-items:center}
.wpb-gate .wpb-g{font-family:var(--wpb-mono);font-size:10.5px;color:var(--wpb-muted);text-transform:uppercase;letter-spacing:.05em}
.wpb-swatches{display:flex;flex-wrap:wrap;gap:8px}
.wpb-swatch{width:96px;border:1px solid var(--wpb-line);border-radius:var(--wpb-r);overflow:hidden;font-size:11px}
.wpb-swatch i{display:block;height:30px} .wpb-swatch div{padding:3px 7px}
.wpb-empty{padding:28px 18px;display:flex;flex-direction:column;gap:10px;align-items:flex-start}
.wpb-set{display:grid;grid-template-columns:200px minmax(0,1fr);gap:8px 18px;padding:12px 14px;border-top:1px solid var(--wpb-line);align-items:start}
.wpb-set:first-child{border-top:0}
.wpb-set .wpb-lbl b{display:block;font-weight:560} .wpb-set .wpb-lbl span{font-size:12px;color:var(--wpb-muted)}
.wpb-rights{display:flex;flex-wrap:wrap;gap:6px}
.wpb-activity{margin-top:22px}
.wpb-acthead{background:none;border:0;padding:4px 0;display:inline-flex;gap:6px;align-items:center;color:var(--wpb-muted);font-size:12px;cursor:pointer}
.wpb-acthead:hover{color:var(--wpb-fg)}
.wpb-actlist{margin-top:6px;border-top:1px solid var(--wpb-line)}
.wpb-act{display:grid;grid-template-columns:92px minmax(0,1fr);gap:4px 12px;padding:7px 0;border-bottom:1px solid var(--wpb-line);font-size:12px}
.wpb-act .wpb-when{color:var(--wpb-faint);font-family:var(--wpb-mono);font-size:11px;padding-top:1px}
.wpb-act-refused b{color:var(--wpb-warn)} .wpb-act-error b{color:var(--wpb-bad)}
.wpb-ver{display:flex;justify-content:space-between;gap:8px;padding:7px 14px;border-top:1px solid var(--wpb-line);font-size:12px;flex-wrap:wrap}
.wpb-yaml{font:11.5px/1.55 var(--wpb-mono);padding:10px 14px;margin:0;overflow-x:auto;white-space:pre-wrap;overflow-wrap:anywhere;border-top:1px solid var(--wpb-line);color:var(--wpb-fg)}
.wpb-kv{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 12px;font-size:12px;margin:0}
.wpb-kv dt{color:var(--wpb-muted)} .wpb-kv dd{margin:0;overflow-wrap:anywhere}
.wpb-skel{background:var(--wpb-sub2);border-radius:var(--wpb-r);animation:wpb-pulse 1.4s ease-in-out infinite}
@keyframes wpb-pulse{50%{opacity:.5}}
.wpb-dcard{display:grid;grid-template-columns:140px minmax(0,1fr);border:1px solid var(--wpb-line);border-radius:var(--wpb-r);overflow:hidden;margin:10px 0;max-width:640px;container-type:inline-size}
.wpb-dcard .wpb-th{background:var(--wpb-sub);padding:8px;display:flex;align-items:flex-start}
.wpb-dcard .wpb-th img{width:100%;height:auto;border-radius:3px;border:1px solid var(--wpb-line);display:block}
.wpb-dcard .wpb-bd{padding:10px 12px;display:flex;flex-direction:column;gap:8px;min-width:0}
.wpb-gcard{padding:12px 14px;display:flex;flex-direction:column;gap:9px}
.wpb-gcard[data-tone=bad]{border-color:color-mix(in srgb,var(--wpb-bad) 45%,var(--wpb-line))}
.wpb-gcard[data-tone=confirm]{border-color:color-mix(in srgb,var(--wpb-acc) 55%,var(--wpb-line));box-shadow:inset 3px 0 0 var(--wpb-acc)}
.wpb-ghead{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.wpb-ghead .wpb-gn{font-family:var(--wpb-mono);font-size:11px;color:var(--wpb-muted);white-space:nowrap}
.wpb-ghead b{font-weight:600;overflow-wrap:anywhere;min-width:0}
.wpb-ghead .wpb-sp{flex:1}
.wpb-gmsg{max-width:72ch;overflow-wrap:anywhere}
.wpb-gmsg code,.wpb-code{font-family:var(--wpb-mono);font-size:11.5px;background:var(--wpb-sub2);border-radius:4px;padding:1px 5px;overflow-wrap:anywhere}
.wpb-confirm{font-size:14px;line-height:1.55;max-width:66ch}
.wpb-limit{display:flex;gap:7px;align-items:flex-start;font-size:12.5px;color:color-mix(in srgb,var(--wpb-fg) 74%,transparent);max-width:72ch;padding-top:8px;border-top:1px solid var(--wpb-line)}
.wpb-limit .codicon{margin-top:3px;flex:none}
.wpb-guide{padding:14px 16px;display:flex;flex-direction:column;gap:12px}
.wpb-guide ol{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:12px;counter-reset:wpbstep}
.wpb-guide li{display:grid;grid-template-columns:26px minmax(0,1fr);gap:10px;counter-increment:wpbstep}
.wpb-guide li::before{content:counter(wpbstep);width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font:600 11px var(--wpb-mono);border:1px solid var(--wpb-line2)}
.wpb-guide li>div{display:flex;flex-direction:column;gap:6px;min-width:0;padding-top:2px}
.wpb-levels{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:5px 14px;font-size:12px;padding:8px 10px;background:var(--wpb-sub);border-radius:var(--wpb-r);margin:0}
.wpb-levels dt{font-weight:600} .wpb-levels dd{margin:0;color:var(--wpb-muted)}
.wpb-levels .wpb-no{color:var(--wpb-bad)}
.wpb-field{display:flex;flex-direction:column;gap:5px;min-width:0}
.wpb-field label{font-size:12px;font-weight:560}
.wpb-input{font:inherit;font-size:12.5px;color:var(--wpb-fg);background:var(--wpb-panel);border:1px solid var(--wpb-line2);border-radius:var(--wpb-r);padding:5px 8px;min-width:0;width:100%}
.wpb-input:focus-visible{outline:2px solid var(--wpb-acc);outline-offset:1px}
.wpb-inline{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.wpb-inline .wpb-input{flex:1 1 220px;width:auto}
.wpb-steps{display:flex;flex-direction:column;gap:6px;font-size:12px;padding:10px;border-radius:var(--wpb-r);background:var(--wpb-sub)}
.wpb-steps>div{display:flex;gap:8px;align-items:flex-start}
.wpb-steps .wpb-mk{font-family:var(--wpb-mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;min-width:44px;padding-top:1px;color:var(--wpb-muted)}
.wpb-steps .wpb-mk-ok{color:var(--wpb-ok)} .wpb-steps .wpb-mk-fail{color:var(--wpb-bad)} .wpb-steps .wpb-mk-run{color:var(--wpb-acc)}
.wpb-check-box{display:inline-flex;gap:7px;align-items:center;font-size:12px;cursor:pointer}
.wpb-check-box input{margin:0;accent-color:var(--wpb-acc)}
.wpb-publine{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.wpb-publine .wpb-input{width:auto;flex:0 1 210px}
@container wpb (max-width: 900px){
 .wpb-grid2,.wpb-desk{grid-template-columns:minmax(0,1fr)}
 .wpb-shots{grid-template-columns:minmax(0,1fr)}
 .wpb-set{grid-template-columns:minmax(0,1fr)}
}
@container wpb (max-width: 480px){
 .wpb-in{padding:14px 12px 40px}
 .wpb-stats>div{padding:6px 8px}
 .wpb-check{grid-template-columns:72px minmax(0,1fr)}
 .wpb-check .wpb-act{grid-column:2}
 .wpb-strip>div{min-width:96px}
 .wpb-shots.wpb-single .wpb-frame{max-width:100%}
}
@media (max-width: 520px){ .wpb-dcard{grid-template-columns:minmax(0,1fr)} .wpb-dcard .wpb-th{display:none} }
@media (prefers-reduced-motion: reduce){ .wpb-skel{animation:none} .wpb-frame,.wpb-switch .wpb-tr,.wpb-switch .wpb-tr::after{transition:none} }
`

function installStyle() {
  if (typeof document === 'undefined') return () => {}
  let node = document.getElementById(STYLE_ID)
  if (!node) {
    node = document.createElement('style')
    node.id = STYLE_ID
    document.head.appendChild(node)
  }
  node.textContent = CSS
  return () => {
    const n = document.getElementById(STYLE_ID)
    if (n) n.remove()
  }
}

// ── backend ──────────────────────────────────────────────────────────────────────────────────────

// Desktop's ctx.rest rejects every HTTP >= 400 with "<status>: <body>" inside the message (Electron adds a
// prefix), so the status and the JSON body after the first "{" are read back out of it.
function restError(error) {
  const message = error && error.message ? String(error.message) : String(error)
  const found = message.match(/\b([1-5]\d\d): ([\s\S]*)$/)
  const status = found ? Number(found[1]) : Number(error && error.status) || 0
  const raw = found ? found[2] : message
  let body = null
  const start = raw.indexOf('{')
  if (start >= 0) {
    try {
      body = JSON.parse(raw.slice(start))
    } catch {
      body = null
    }
  }
  const notOn = status === 404 && /plugin not found|not enabled/i.test(raw)
  const text = notOn
    ? 'WPBrücke is not on in this profile.'
    : (body && (body.message || (typeof body.detail === 'string' ? body.detail : null))) || raw || 'Request failed'
  const out = new Error(text)
  out.status = status
  out.body = body
  out.notOn = notOn
  return out
}

async function rest(path, opts) {
  if (!pluginCtx || typeof pluginCtx.rest !== 'function') throw new Error('This Hermes build cannot reach plugin routes.')
  try {
    return await pluginCtx.rest(path, { timeoutMs: 30000, ...(opts || {}) })
  } catch (error) {
    const out = restError(error)
    log('rest ' + ((opts && opts.method) || 'GET') + ' ' + path + ' failed', { status: out.status, message: out.message })
    throw out
  }
}

const enc = encodeURIComponent

function useProfile() {
  return useValue(host.state.profile) || ''
}

const qk = (profile, ...rest) => [ID, profile, ...rest]
const retryOnce = (failures, error) => !(error && (error.notOn || error.status === 404 || error.status === 403)) && failures < 1

function useRoute_(key, path, opts) {
  const profile = useProfile()
  return useQuery({
    queryKey: qk(profile, ...key),
    queryFn: () => rest(path),
    refetchInterval: POLL_MS,
    staleTime: 10000,
    retry: retryOnce,
    ...(opts || {})
  })
}

// Opening Hermes' settings form unmounts the page; coming back must read the groups and sites again
// (a cached answer from before the settings were saved would hide the new group), so these two
// refetch on every mount.
const useOverview = () => useRoute_(['overview'], '/overview', { refetchOnMount: 'always' })
const useGroups = () => useRoute_(['groups'], '/groups', { refetchOnMount: 'always' })
const useSite = site => useRoute_(['site', site], '/sites/' + enc(site), { enabled: Boolean(site) })
const useDesk = site => useRoute_(['desk', site || ''], '/desk' + (site ? '?site=' + enc(site) : ''))
const useDeskItem = item => useRoute_(['item', item], '/desk/' + enc(item), { enabled: Boolean(item) })
const useSeo = site => useRoute_(['seo', site], '/seo?site=' + enc(site), { enabled: Boolean(site) })
// The write log is read only while the Activity disclosure is open (it stays in the background, A35).
const useActivity = (site, open) => useRoute_(['activity', site], '/sites/' + enc(site) + '/activity', { enabled: Boolean(site) && open })
const useBuilds = (site, enabled) => useRoute_(['build', site], '/build?site=' + enc(site), { enabled: Boolean(site) && enabled })

function useInvalidate() {
  const client = useQueryClient()
  const profile = useProfile()
  // Every view of this profile, except the job polls (a finished job is never read again).
  return () => client.invalidateQueries({ queryKey: qk(profile), predicate: q => q.queryKey[2] !== 'job' })
}

// A long job (proof, SEO site check): POST starts it, GET /jobs/{id} polls every 2 s until done or failed.
// A 409 "busy" carries retry_after_s; it is shown, never retried on its own.
function useJob(onDone) {
  const profile = useProfile()
  const [job, setJob] = useState(null)
  const [problem, setProblem] = useState(null)
  const [starting, setStarting] = useState(false)
  const id = job && job.id
  const q = useQuery({
    queryKey: qk(profile, 'job', id || ''),
    queryFn: () => rest('/jobs/' + enc(id)),
    enabled: Boolean(id),
    refetchInterval: query => {
      const data = query && query.state ? query.state.data : query
      const state = data && data.job && data.job.state
      return state === 'done' || state === 'failed' ? false : JOB_POLL_MS
    },
    retry: retryOnce
  })
  const current = (q.data && q.data.job) || job
  const state = current && current.state
  useEffect(() => {
    if (state === 'done' || state === 'failed') onDone && onDone(current)
  }, [state])
  const start = async (path, body) => {
    setProblem(null)
    setStarting(true)
    try {
      const res = await rest(path, { method: 'POST', body, timeoutMs: 15000 })
      if (res && res.ok === false) setProblem(res.message || 'The job did not start.')
      else setJob((res && res.job) || null)
    } catch (error) {
      if (error.status === 409 && error.body && error.body.error === 'busy') {
        const s = Number(error.body.retry_after_s)
        setProblem('The proof browser is busy, retry in ' + (Number.isFinite(s) ? Math.max(1, Math.round(s)) : 'a few') + ' s.')
      } else setProblem(error.message)
    } finally {
      setStarting(false)
    }
  }
  const running = starting || state === 'queued' || state === 'running'
  return { job: current, problem, running, start }
}

// ── host helpers ─────────────────────────────────────────────────────────────────────────────────

function openExternal(href) {
  if (!href || !/^https?:\/\//i.test(href)) return
  const os = pluginCtx && pluginCtx.os
  Promise.resolve(os && typeof os.openExternal === 'function' ? os.openExternal(href) : false)
    .then(ok => {
      if (!ok) window.open(href, '_blank', 'noopener')
    })
    .catch(() => window.open(href, '_blank', 'noopener'))
}

// hermes://open/<page>?q or hermes://<plugin>/<path>?q -> an in-app route; http(s) -> the browser.
function openTarget(href) {
  if (!href) return
  const m = String(href).match(/^hermes:\/\/([a-z0-9][a-z0-9-]*)\/([^\s]*)$/)
  if (m) {
    host.navigate(m[1] === 'open' ? '/' + m[2] : '/' + m[1] + '/' + m[2])
    return
  }
  openExternal(href)
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

// A fresh chat with the text in its composer. Never submits: the person reads it and presses send.
async function draftInChat(text) {
  try {
    if (typeof host.newChat === 'function') host.newChat()
    else host.navigate('/')
  } catch {
    host.navigate('/')
  }
  const composer = host.composer
  for (let i = 0; i < 16; i++) {
    await sleep(i === 0 ? 120 : 250)
    try {
      if (composer && (await composer.setDraft(null, text))) return true
    } catch {
      /* the composer is not mounted yet */
    }
  }
  const os = pluginCtx && pluginCtx.os
  if (os && typeof os.writeClipboard === 'function') await os.writeClipboard(text)
  host.notify({ kind: 'info', message: 'The chat composer did not answer; the text is on the clipboard: ' + text })
  return false
}

// ── formatting ───────────────────────────────────────────────────────────────────────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function dayMonth(iso) {
  const t = Date.parse(iso || '')
  if (!Number.isFinite(t)) return '—'
  const d = new Date(t)
  return d.getDate() + ' ' + MONTHS[d.getMonth()]
}

function ago(iso, short) {
  const t = Date.parse(iso || '')
  if (!Number.isFinite(t)) return ''
  const s = Math.max(0, Math.round((Date.now() - t) / 1000))
  const tail = short ? '' : ' ago'
  if (s < 60) return 'now'
  if (s < 3600) return Math.round(s / 60) + ' min' + tail
  if (s < 86400) return Math.round(s / 3600) + ' h' + tail
  if (s < 2 * 86400) return 'yesterday'
  if (s < 14 * 86400) return Math.round(s / 86400) + ' d' + tail
  return dayMonth(iso)
}

const KIND = {
  new_post: ['new post', 'drafts'],
  new_page: ['new page', 'drafts'],
  staged_edit: ['change', 'staged'],
  seo_fix: ['SEO fix', 'seo'],
  build_page: ['build page', 'build']
}
const kindLabel = item => (item.kind === 'staged_edit' ? (item.wp_type === 'page' ? 'page change' : 'post change') : (KIND[item.kind] || [item.kind])[0])
const kindGroup = item => (KIND[item.kind] || [null, 'drafts'])[1]
const STATE = {
  waiting: ['waiting', 'wait'],
  changes_requested: ['changes asked', 'wait'],
  reviewed: ['reviewed', 'done'],
  publish_requested: ['publish link opened', 'wait'],
  published: ['published', 'done'],
  scheduled: ['scheduled', 'done'],
  discarded: ['discarded', '']
}
const isOpen = item => item.state === 'waiting' || item.state === 'changes_requested'
const byWho = who => (who === 'person' ? 'You' : 'Hermes')
const VERDICT = { match: 'match', differs: 'differs', not_proven: 'not proven' }
const verdictOf = v => (VERDICT[v] ? v : 'not_proven')

const CHECK_WORDS = {
  title_length: 'Title length', description: 'Meta description', headings: 'Headings', internal_links: 'Internal links',
  alt_text: 'Image alt text', slug: 'Slug', readability: 'Readability', indexing: 'Indexing', sitemap: 'Sitemap',
  robots_txt: 'robots.txt', mobile: 'Mobile layout', speed: 'Speed', search_queries: 'Search queries', canonical: 'Canonical'
}
const checkLabel = key => CHECK_WORDS[key] || String(key || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())

function widthName(w) {
  const n = Number(w)
  if (n >= 1024) return 'Desktop'
  if (n >= 600) return 'Tablet'
  return 'Mobile'
}
const frameClass = w => ({ Desktop: '', Tablet: 'wpb-tablet', Mobile: 'wpb-mobile' })[widthName(w)]

// A desk Publish, Schedule or Apply answer as one line: the route's sentence plus what WordPress reads
// back (status, and the date of a scheduled post).
function readBack(action, res) {
  const verb = { publish: 'Published', schedule: 'Scheduled', apply: 'Applied' }[action] || 'Done'
  const rb = (res && res.read_back) || {}
  const item = (res && res.item) || {}
  const status = rb.status || item.live_status || (res && res.status)
  const date = rb.date || (res && res.date)
  const head = (res && res.message) || verb + '.'
  return head + (status ? ' WordPress reads it back as ' + status + (action === 'schedule' && date ? ' for ' + date : '') + '.' : '')
}

const safeImg = src => (typeof src === 'string' && /^data:image\/(png|jpeg|webp);base64,/.test(src) ? src : null)

function host_(url) {
  return String(url || '').replace(/^https?:\/\//, '').replace(/\/$/, '')
}

function cronWords(expr) {
  const m = String(expr || '').trim().match(/^(\d{1,2}) (\d{1,2}) \* \* ([0-6])$/)
  if (!m) return expr ? 'schedule ' + expr : 'no schedule'
  const day = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][Number(m[3])]
  return 'Every ' + day + ' ' + m[2].padStart(2, '0') + ':' + m[1].padStart(2, '0')
}

// ── route state: ?view=home|desk|seo|build|settings|site&site=&item= on the hash route ───────────

function parseHash() {
  const raw = String((typeof window !== 'undefined' && window.location && window.location.hash) || '').replace(/^#/, '')
  const at = raw.indexOf('?')
  const path = at >= 0 ? raw.slice(0, at) : raw
  if (path !== PATH) return null
  const s = new URLSearchParams(at >= 0 ? raw.slice(at + 1) : '')
  const view = s.get('view') || 'home'
  const site = s.get('site') || ''
  const item = s.get('item') || ''
  return { view: VIEWS.includes(view) ? view : 'home', site: SITE_RE.test(site) ? site : '', item: ITEM_RE.test(item) ? item : '' }
}

function routeHref(r) {
  const s = new URLSearchParams()
  if (r.view && r.view !== 'home') s.set('view', r.view)
  if (r.site) s.set('site', r.site)
  if (r.item) s.set('item', r.item)
  const q = s.toString()
  return PATH + (q ? '?' + q : '')
}

function useRoute() {
  const [route, setRoute] = useState(() => parseHash() || { view: 'home', site: '', item: '' })
  useEffect(() => {
    const on = () => {
      const next = parseHash()
      if (next) setRoute(next)
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const go = next => {
    const r = { view: next.view || 'home', site: next.site || '', item: next.item || '' }
    setRoute(r)
    host.navigate(routeHref(r))
  }
  return [route, go]
}

// ── small pieces ─────────────────────────────────────────────────────────────────────────────────

function Btn({ kind, onClick, disabled, children, title, test, icon, ext }) {
  return el('button', { className: cx('wpb-btn', kind && 'wpb-btn-' + kind), disabled, onClick, title, type: 'button', 'data-test': test },
    icon ? el(Codicon, { name: icon, size: '0.8rem' }) : null, children, ext ? ' ↗' : null)
}

function Badge({ tone, children, title }) {
  return el('span', { className: cx('wpb-badge', tone && 'wpb-badge-' + tone), title }, children)
}

function Dot({ tone }) {
  return el('span', { className: cx('wpb-dot', tone && 'wpb-dot-' + tone), 'aria-hidden': 'true' })
}

function ProofChip({ verdict, text }) {
  const v = verdictOf(verdict)
  return el('span', { className: 'wpb-proof wpb-proof-' + v, 'data-verdict': v }, el('span', { className: 'wpb-s' }, VERDICT[v]), el('span', {}, text))
}

function Who({ who }) {
  const w = ['hermes', 'you', 'client'].includes(who) ? who : 'you'
  return el('span', { className: 'wpb-who wpb-who-' + w }, { hermes: 'Hermes', you: 'You', client: 'Client' }[w])
}

function SwitchDisplay({ on, label, onClick, disabled, test, busy }) {
  return el('button', { className: 'wpb-switch', role: 'switch', 'aria-checked': Boolean(on), onClick, disabled: disabled || busy, type: 'button', 'data-test': test },
    el('span', { className: 'wpb-tr' }), el('span', { className: 'wpb-small' }, label))
}

function Seg({ options, value, onChange, label, test }) {
  return el('div', { className: 'wpb-seg', role: 'group', 'aria-label': label, 'data-test': test },
    options.map(o => el('button', { key: o.id, 'aria-pressed': o.id === value, disabled: o.disabled, onClick: () => onChange && !o.disabled && onChange(o.id), title: o.title, type: 'button' }, o.label)))
}

function Skel({ h }) {
  return el('div', { className: 'wpb-skel', style: { height: h || 80 } })
}

function Loading({ text }) {
  return el('div', { className: 'wpb-stack', 'data-test': 'loading' }, el('div', { className: 'wpb-small wpb-muted' }, text || 'Reading your sites.'), el(Skel, { h: 70 }), el(Skel, { h: 140 }), el(Skel, { h: 140 }))
}

function Problem({ title, text, actions, test }) {
  return el('div', { className: 'wpb-panel wpb-empty', 'data-test': test || 'error' },
    el('b', {}, title), text ? el('span', { className: 'wpb-muted wpb-small' }, text) : null, actions ? el('div', { className: 'wpb-actions' }, actions) : null)
}

function NotOn() {
  return el(Problem, {
    test: 'not-on',
    title: 'WPBrücke is not on in this profile',
    text: 'The page reads its data from the WPBrücke plugin of the profile this chat runs on. Turn the plugin on there (Capabilities, Plugins), then come back; nothing on your sites changed.',
    actions: [el(Btn, { key: 'p', kind: 'primary', onClick: () => host.navigate('/capabilities?tab=plugins') }, 'Open Capabilities, Plugins')]
  })
}

function QueryProblem({ q, what }) {
  const error = q.error || {}
  if (error.notOn) return el(NotOn, {})
  return el(Problem, { title: what + ' could not load', text: 'Hermes did not answer: ' + error.message + '. Nothing on your sites changed.', actions: [el(Btn, { key: 'r', onClick: () => q.refetch() }, 'Try again')] })
}

// 200 {ok:false, error, message, setting}: a config or key problem with the setting that fixes it.
function SetupProblem({ data }) {
  return el(Problem, {
    test: 'setup-problem',
    title: 'WPBrücke needs one more setting',
    text: data.message + (data.setting ? ' (' + data.setting + ')' : ''),
    actions: [el(Btn, { key: 's', kind: 'primary', onClick: () => host.navigate(settingsHref(host.state && host.state.profile && typeof host.state.profile.get === 'function' ? host.state.profile.get() : '')) }, 'Open the plugin settings')]
  })
}

function guardData(q, what) {
  if (q.isLoading) return el(Loading, {})
  if (q.isError) return el(QueryProblem, { q, what })
  if (q.data && q.data.ok === false) return el(SetupProblem, { data: q.data })
  return null
}

function healthOf(row) {
  if (!row.reachable) return ['bad', 'did not answer']
  if ((row.rest_ms || 0) > 1500) return ['warn', 'REST slow, ' + (row.rest_ms / 1000).toFixed(1) + ' s']
  if (row.noindex) return ['warn', 'noindex: hidden from search']
  return ['ok', 'REST ' + ((row.rest_ms || 0) / 1000).toFixed(2) + ' s']
}

const LEVEL_NAME = { read_public: 'Public reads', read: 'Read', drafts: 'Drafts', publish: 'Publish' }

// A site row's access as one badge: the level the person confirmed, or what stops it. A row without a
// group is a public site (no password).
function accessOf(row) {
  if (!row || !row.group || typeof row.group !== 'number') return { tone: null, text: 'Public site', act: 'connect' }
  if (row.effective === 'stopped') return { tone: 'bad', text: 'Stopped', act: 'confirm' }
  if (row.effective === 'unconfirmed' || !row.level) return { tone: 'warn', text: 'Confirm needed', act: 'confirm' }
  if (row.effective === 'lowered') return { tone: 'warn', text: 'Lowered from ' + (LEVEL_NAME[row.level] || row.level) }
  return { tone: 'good', text: LEVEL_NAME[row.level] || row.level }
}

// The site's own group label ("Clients") when the row carries it; a number is the Site N group.
const labelGroup = row => (typeof row.label_group === 'string' ? row.label_group : typeof row.group === 'string' ? row.group : '')

// ── connector, health and builder on a site ──────────────────────────────────────────────────────

const versionParts = v => String(v || '').split(/[.+-]/).slice(0, 3).map(n => parseInt(n, 10) || 0)

// 'ok', 'older' (below the version this plugin ships), 'missing', or 'unread' (installed, version not
// readable without a key).
function connectorState(version, present) {
  if (!version) return present ? 'unread' : 'missing'
  const [a, b] = [versionParts(version), versionParts(CONNECTOR_NEEDED)]
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] < b[i] ? 'older' : 'ok'
  return 'ok'
}

const installHref = siteUrl => String(siteUrl || '').replace(/\/+$/, '') + '/wp-admin/plugin-install.php?tab=upload'

function InstallLink({ url, kind }) {
  if (!/^https?:\/\//i.test(String(url || ''))) return null
  return el(Btn, { kind, onClick: () => openExternal(installHref(url)), ext: true, test: 'install-connector', title: 'WordPress: Plugins, Add New, Upload Plugin' }, 'Install in WordPress')
}

const HEALTH_BADGE = {
  broken: ['bad', 'Broken', 'The home page answers with an error'],
  updating: ['warn', 'Updating', 'WordPress shows its maintenance page while an update runs']
}

const BUILDER_NAME = {
  elementor: 'Elementor', divi: 'Divi', wpbakery: 'WPBakery', beaver: 'Beaver Builder', bricks: 'Bricks',
  oxygen: 'Oxygen', breakdance: 'Breakdance', unknown_builder: 'Unknown builder'
}
// The block editor and the classic editor are WordPress itself, not a page builder.
const NOT_A_BUILDER = ['blocks', 'classic']

// Health when it is not ok, and each page builder with "no pack" when its pack is off. A key the
// backend does not send yet renders nothing.
function SiteBadges({ row }) {
  const out = []
  const h = row.health && HEALTH_BADGE[row.health.verdict]
  if (h) {
    const since = Number(row.health.since)
    const when = Number.isFinite(since) && since > 0 ? ago(new Date(since * 1000).toISOString()) : ''
    out.push(el(Badge, { key: 'health', tone: h[0], title: h[2] + (when ? ', since ' + when : '') }, el('span', { 'data-test': 'health-badge' }, h[1])))
  }
  const packs = row.packs && typeof row.packs === 'object' ? row.packs : null
  for (const id of Array.isArray(row.builders) ? row.builders : []) {
    if (typeof id !== 'string' || NOT_A_BUILDER.includes(id)) continue
    const pack = packs && packs[id] && typeof packs[id] === 'object' ? packs[id] : null
    const name = (pack && typeof pack.label === 'string' && pack.label) || BUILDER_NAME[id] || id
    const noPack = packs !== null && !(pack && pack.on)
    out.push(el(Badge, { key: 'b-' + id, title: noPack ? 'Without a pack for this builder Hermes does not write the body of these pages' : undefined },
      el('span', { 'data-test': 'builder-badge' }, noPack ? name + ' · no pack' : name)))
  }
  return out.length ? out : null
}

// ── Needs you (aside) ────────────────────────────────────────────────────────────────────────────

function NeedsYou({ needs, go, note }) {
  return el('aside', { className: 'wpb-panel wpb-side', 'data-test': 'needs-you' },
    el('h2', { className: 'wpb-h2' }, 'Needs you', el('span', { className: 'wpb-xs wpb-muted wpb-mono' }, String(needs.length))),
    needs.length
      ? needs.map((n, i) => el('button', { key: i, className: 'wpb-need', onClick: () => go(n.go || { view: 'home' }), type: 'button', 'data-kind': n.kind },
        el('span', { className: 'wpb-w' }, el('b', {}, n.title), n.detail ? el('span', {}, n.detail) : null),
        el('span', { className: cx('wpb-when', n.urgent && 'wpb-urgent') }, n.urgent ? 'now' : ago(n.when, true))))
      : el('div', { className: 'wpb-side-note' }, 'Nothing waits for you. Drafts, staged changes and SEO findings show up here.'),
    note ? el('div', { className: 'wpb-side-note' }, note) : null)
}

// ── Activity: the write log at the foot of the site page, closed until opened ─────────────────────

const ACTIVITY_WORDS = {
  create: 'Draft created', update: 'Draft changed', stage: 'Change staged as a draft copy', set_seo: 'SEO fields set',
  publish: 'Published', schedule: 'Scheduled', apply: 'Staged change applied', upload_media: 'Image uploaded',
  stage_fix: 'SEO fix staged', set_brand: 'Brand colours staged', add_site: 'Site file added', update_site: 'Site file changed',
  restore_site: 'Site file restored', remove_site: 'Site file removed', add_public_site: 'Public site added',
  confirm_level: 'Access level confirmed', revoke_password: 'Application password revoked', mark_reviewed: 'Marked reviewed',
  ask_changes: 'Changes asked for', note: 'Note added', request_publish: 'Publish asked for', record_user: 'WordPress user recorded'
}

function activityWho(who) {
  const w = String(who || '')
  if (w === 'person') return 'you'
  if (w.startsWith('automation')) return 'an automation'
  if (w === 'unattended') return 'an unattended run'
  if (w === 'check') return 'the connection check'
  return 'Hermes'
}

function activityWhat(line) {
  const words = ACTIVITY_WORDS[line.action] || String(line.action || 'write').replace(/_/g, ' ')
  if (line.result === 'refused') return 'Refused: ' + words.charAt(0).toLowerCase() + words.slice(1) + (line.code ? ' (' + line.code + ')' : '')
  if (line.result === 'error') return 'Failed: ' + words.charAt(0).toLowerCase() + words.slice(1) + (line.code ? ' (' + line.code + ')' : '')
  return words
}

function activityTarget(line) {
  const kind = line.wp_type && !['site_file', 'access', 'application_password', 'brand'].includes(line.wp_type) ? line.wp_type : ''
  const label = line.label ? '“' + line.label + '”' : ''
  return [label, kind && line.wp_id != null ? kind + ' ' + line.wp_id : ''].filter(Boolean).join(' · ')
}

function ActivityRow({ line }) {
  const target = activityTarget(line)
  return el('div', { className: cx('wpb-act', line.result !== 'ok' && 'wpb-act-' + line.result), 'data-test': 'activity-line' },
    el('span', { className: 'wpb-when', title: line.ts || '' }, ago(line.ts)),
    el('span', { style: { minWidth: 0, overflowWrap: 'anywhere' } },
      el('b', {}, activityWhat(line)), target ? ' ' + target : '', el('span', { className: 'wpb-muted' }, ', by ' + activityWho(line.who)),
      line.summary ? el('span', { className: 'wpb-xs wpb-faint', style: { display: 'block' } }, line.summary) : null))
}

function Activity({ site }) {
  const [open, setOpen] = useState(false)
  const q = useActivity(site, open)
  const lines = (q.data && q.data.lines) || []
  let body = null
  if (open) {
    if (q.isLoading) body = el('div', { style: { paddingTop: 8 } }, el(Skel, { h: 40 }))
    else if (q.error) body = el('p', { className: 'wpb-small wpb-muted', style: { paddingTop: 8 } }, 'The write log could not be read: ' + q.error.message)
    else if (!lines.length) body = el('p', { className: 'wpb-small wpb-muted', style: { paddingTop: 8 } }, 'Nothing logged yet. Every change WPBrücke makes or refuses on this site shows up here.')
    else body = el('div', { className: 'wpb-actlist', 'data-test': 'activity-list' }, lines.map((line, i) => el(ActivityRow, { key: i, line })))
  }
  return el('section', { className: 'wpb-activity', 'data-test': 'activity', 'data-open': open ? 'true' : 'false' },
    el('button', { className: 'wpb-acthead', type: 'button', 'aria-expanded': open, onClick: () => setOpen(!open), 'data-test': 'activity-toggle' },
      el(Codicon, { name: open ? 'chevron-down' : 'chevron-right', size: '0.9rem' }), 'Activity'),
    open ? el('p', { className: 'wpb-xs wpb-faint' }, 'What WPBrücke changed or refused on this site, newest first. Hermes reads the same log when you ask what happened.') : null,
    body)
}

// ── Home: one site ───────────────────────────────────────────────────────────────────────────────

function SiteHome({ row, needs, go, many }) {
  const desk = useDesk(row.id)
  const site = useSite(row.id)
  const seo = useSeo(row.id)
  const items = ((desk.data && desk.data.items) || []).filter(isOpen)
  const autos = (site.data && site.data.site && site.data.site.automations) || []
  const score = seo.data && seo.data.site_report ? seo.data.site_report.score : null
  const [tone, health] = healthOf(row)
  const access = accessOf(row)
  const publishes = row.level === 'publish' && (row.effective === 'confirmed' || !row.effective)
  const newDraft = () => draftInChat('Write a blog draft for @wp:' + row.id + ' about ')
  return el('div', { 'data-test': 'site-home', 'data-site': row.id },
    many ? el('div', { className: 'wpb-crumb' }, el('button', { className: 'wpb-link', onClick: () => go({ view: 'home' }), type: 'button' }, 'Fleet'), el('span', { className: 'wpb-muted' }, '›'), el('span', {}, row.label)) : null,
    el('div', { className: 'wpb-panel wpb-hero' },
      el('div', { className: 'wpb-id' },
        el('span', { className: 'wpb-mark' }, row.mark || '··'),
        el('div', { style: { minWidth: 0 } },
          el('b', {}, row.label),
          el('div', { className: 'wpb-xs wpb-muted', style: { overflowWrap: 'anywhere' } }, host_(row.url)),
          el('div', { className: 'wpb-meta' },
            el(Badge, {}, row.wp_version ? 'WordPress ' + row.wp_version : 'WordPress version hidden'),
            el(Badge, {}, 'Theme: ' + (row.theme || 'unknown')),
            el('span', { className: 'wpb-health' }, el(Dot, { tone }), health),
            el(SiteBadges, { row }),
            el(Badge, { tone: access.tone }, 'Access: ' + access.text)))),
      el('div', { className: 'wpb-actions' },
        el(Btn, { kind: 'primary', onClick: newDraft, test: 'new-draft', icon: 'sparkle' }, 'New blog draft with Hermes'),
        el(Btn, { onClick: () => openExternal(row.wp_admin_url), ext: true }, 'Open wp-admin'),
        connectorState(row.connector) === 'ok' ? null : el(InstallLink, { url: row.url }))),
    el('div', { className: 'wpb-strip', 'data-test': 'strip' },
      el('div', {}, el('b', {}, String(row.desk_waiting || 0)), el('span', {}, 'waiting for you')),
      el('div', {}, el('b', {}, row.drafts == null ? '—' : String(row.drafts)), el('span', {}, 'drafts')),
      el('div', {}, el('b', {}, row.published == null ? '—' : String(row.published)), el('span', {}, 'published')),
      el('div', {}, el('b', {}, score == null ? '—' : String(score)), el('span', {}, 'SEO score')),
      el('div', {}, el('b', {}, row.updates == null ? '—' : String(row.updates)), el('span', {}, 'updates'))),
    el('div', { className: 'wpb-grid2' },
      el('div', { className: 'wpb-stack' },
        el('div', { className: 'wpb-panel wpb-list', 'data-test': 'drafts' },
          el('h2', { className: 'wpb-h2' }, 'Waiting on the desk'),
          desk.isLoading ? el('div', { style: { padding: '0 14px 14px' } }, el(Skel, { h: 60 }))
            : items.length
              ? items.map(it => el('div', { key: it.id, className: 'wpb-row' },
                el('span', {}, el('b', {}, it.title), ' ', el('span', { className: 'wpb-type' }, kindLabel(it)),
                  el('br', {}), el('span', { className: 'wpb-xs wpb-muted' }, byWho(it.created_by) + ', ' + ago(it.created_at) + ' · proof ', el('span', { className: 'wpb-v-' + verdictOf(it.proof_verdict) }, VERDICT[verdictOf(it.proof_verdict)]))),
                el(Btn, { onClick: () => go({ view: 'desk', site: many ? row.id : '', item: it.id }) }, 'Review')))
              : el('div', { className: 'wpb-row wpb-muted wpb-small' }, 'Nothing waits. Ask Hermes for a draft and it lands here with its proof.')),
        el('div', { className: 'wpb-panel wpb-list', 'data-test': 'recurring' },
          el('h2', { className: 'wpb-h2' }, 'Recurring drafts'),
          autos.length
            ? autos.map(a => el('div', { key: a.id, className: 'wpb-row' },
              el('span', {}, el('b', {}, a.id.replace(/-/g, ' ')), el('br', {}), el('span', { className: 'wpb-xs wpb-muted' }, cronWords(a.schedule) + ' · ' + (a.publish && publishes ? 'publishes the ' + (a.post_type || 'post') : 'lands as a ' + (a.post_type || 'post') + ' draft'))),
              el(Badge, { tone: a.enabled ? null : 'warn' }, a.enabled ? 'Hermes scheduled job' : 'paused')))
            : el('div', { className: 'wpb-row wpb-muted wpb-small' }, 'None yet. Tell Hermes, for example: every Monday, a draft from my notes folder.'))),
      el(NeedsYou, { needs, go, note: levelNote(row) })),
    el(Activity, { site: row.id }))
}

function levelNote(row) {
  const a = accessOf(row)
  if (a.act === 'connect') return 'A public site: Hermes reads what the public sees. Connect it in Connections to let Hermes write.'
  if (a.act === 'confirm') return 'Hermes does nothing with this site\'s password until you confirm its access in Connections.'
  if (row.effective === 'lowered') return 'WordPress narrowed this site\'s Hermes user; Hermes follows the narrower role.'
  if (row.level === 'publish') return 'Hermes can publish here: the Publish level you confirmed.'
  if (row.level === 'drafts') return 'Hermes writes drafts here. You publish in WordPress.'
  return 'Hermes reads this site and never writes.'
}

// ── Home: the fleet ──────────────────────────────────────────────────────────────────────────────

function SiteCard({ row, go }) {
  const [tone, health] = healthOf(row)
  const access = accessOf(row)
  return el('article', { className: 'wpb-panel wpb-site', 'data-test': 'site-card', 'data-site': row.id },
    el('div', { className: 'wpb-site-h' },
      el('span', { className: 'wpb-mark' }, row.mark || '··'),
      el('div', { className: 'wpb-t' },
        el('b', {}, host_(row.url)),
        row.label && row.label !== host_(row.url) ? el('span', { className: 'wpb-xs wpb-muted' }, row.label) : null,
        el('div', { className: 'wpb-meta' },
          el(Badge, {}, 'Theme: ' + (row.theme || 'unknown')),
          el(Badge, {}, row.wp_version ? 'WP ' + row.wp_version : 'WP version hidden'),
          el('span', { className: 'wpb-health' }, el(Dot, { tone }), health),
          el(SiteBadges, { row })))),
    el('div', { className: 'wpb-stats' },
      el('div', {}, el('b', { className: row.desk_waiting ? 'wpb-hot' : '' }, String(row.desk_waiting || 0)), el('span', {}, 'for you')),
      el('div', {}, el('b', {}, row.updates == null ? '—' : String(row.updates)), el('span', {}, 'updates')),
      el('div', {}, el('b', {}, row.last_post ? dayMonth(row.last_post.date) : '—'), el('span', {}, 'last post'))),
    el('div', { className: 'wpb-site-b' },
      el('div', { className: 'wpb-access', 'data-test': 'card-access' },
        el('span', {}, 'Access: ', el('b', {}, access.text)),
        access.act
          ? el(Btn, { onClick: () => go({ view: 'connect' }), test: 'card-' + access.act }, access.act === 'connect' ? 'Connect' : 'Confirm')
          : el('span', { className: 'wpb-xs wpb-muted' }, row.level_words || ''))),
    el('div', { className: 'wpb-site-f' },
      el(Btn, { onClick: () => go({ view: 'site', site: row.id }), test: 'open-site' }, 'Open site'),
      row.cockpit_url ? el(Btn, { kind: 'ghost', onClick: () => openTarget(row.cockpit_url), test: 'cockpit-link' }, 'Coolify') : null,
      el(Btn, { kind: 'ghost', onClick: () => openExternal(row.wp_admin_url), ext: true }, 'wp-admin'),
      connectorState(row.connector) === 'ok' ? null : el(InstallLink, { url: row.url, kind: 'ghost' })))
}

function Fleet({ data, go }) {
  const [group, setGroup] = useState('all')
  const rows = data.sites || []
  const groups = []
  for (const r of rows) {
    const g = labelGroup(r)
    const found = groups.find(x => x.id === g)
    if (found) found.n += 1
    else groups.push({ id: g, n: 1 })
  }
  const shown = rows.filter(r => group === 'all' || labelGroup(r) === group)
  const s = data.strip || {}
  return el('div', { 'data-test': 'fleet' },
    el('div', { className: 'wpb-strip', 'data-test': 'strip' },
      el('div', {}, el('b', {}, String(s.sites ?? rows.length)), el('span', {}, 'sites')),
      el('div', {}, el('b', {}, (s.keyed ?? 0) + '/' + (s.sites ?? rows.length)), el('span', {}, 'connected')),
      el('div', {}, el('b', {}, String(s.waiting ?? 0)), el('span', {}, 'waiting for you')),
      el('div', {}, el('b', {}, String(s.updates ?? 0)), el('span', {}, 'updates')),
      el('div', {}, el('b', {}, String(s.slow ?? 0)), el('span', {}, s.slow === 1 ? 'slow site' : 'slow sites'))),
    groups.length > 1
      ? el('div', { className: 'wpb-filters', 'data-test': 'groups' },
        el('button', { className: 'wpb-chip', 'aria-pressed': group === 'all', onClick: () => setGroup('all'), type: 'button' }, 'All ', el('span', { className: 'wpb-n' }, String(rows.length))),
        groups.map(g => el('button', { key: g.id || '-', className: 'wpb-chip', 'aria-pressed': group === g.id, onClick: () => setGroup(g.id), type: 'button' }, g.id || 'No group', ' ', el('span', { className: 'wpb-n' }, String(g.n)))))
      : null,
    el('div', { className: 'wpb-grid2' },
      el('div', { className: 'wpb-cards' }, shown.map(r => el(SiteCard, { key: r.id, row: r, go }))),
      el(NeedsYou, { needs: data.needs_you || [], go, note: 'Infrastructure (containers, backups, deploys) lives in Coolifybrücke. A card with a Coolify app links there.' })))
}

// ── Review desk ──────────────────────────────────────────────────────────────────────────────────

const FILTERS = [['all', 'All'], ['drafts', 'Drafts'], ['staged', 'Staged'], ['seo', 'SEO'], ['build', 'Build']]

function Queue({ items, counts, filter, setFilter, selected, select, many }) {
  const shown = items.filter(i => filter === 'all' || kindGroup(i) === filter)
  return el('div', { className: 'wpb-panel', 'data-test': 'queue' },
    el('div', { className: 'wpb-qhead' },
      FILTERS.filter(([id]) => id !== 'build' || counts.build).map(([id, label]) =>
        el('button', { key: id, className: 'wpb-chip', 'aria-pressed': filter === id, onClick: () => setFilter(id), type: 'button', 'data-filter': id },
          label, ' ', el('span', { className: 'wpb-n' }, String(counts[id] ?? 0))))),
    shown.length
      ? shown.flatMap((i, n) => {
        const [word, tone] = STATE[i.state] || [i.state, '']
        const sep = !isOpen(i) && (n === 0 || isOpen(shown[n - 1])) ? el('div', { key: 'sep', className: 'wpb-qsep' }, 'Done') : null
        const row = el('button', { key: i.id, className: cx('wpb-qitem', !isOpen(i) && 'wpb-done'), 'aria-selected': i.id === selected, onClick: () => select(i.id), type: 'button', 'data-item': i.id },
          el('div', { className: 'wpb-r1' }, el('span', { className: 'wpb-ttl' }, i.title), el('span', { className: 'wpb-type' }, kindLabel(i))),
          el('div', { className: 'wpb-r2' }, many ? el('span', {}, i.site) : null, el('span', {}, byWho(i.created_by) + ', ' + ago(i.created_at)), el('span', { className: cx('wpb-st', tone && 'wpb-st-' + tone) }, word)))
        return sep ? [sep, row] : row
      })
      : el('div', { className: 'wpb-empty wpb-small wpb-muted' }, filter === 'all' ? 'Nothing on the desk.' : 'Nothing in this filter.'))
}

function ReviewLinkResult({ result, editHref }) {
  if (!result) return null
  if (result.kind === 'opened') {
    return el('div', { className: 'wpb-notice wpb-info', 'data-test': 'link-opened' },
      el('span', {}, result.linkKind === 'editor'
        ? 'Opened the WordPress editor in your browser (the connector review screen is not available).'
        : 'Opened the review screen in your browser. Your own WordPress login does the click there; the desk re-reads the post afterwards.'))
  }
  return el('div', { className: 'wpb-notice', 'data-test': 'link-refused' },
    el('span', {}, el('b', {}, 'No review link: '), result.message),
    editHref ? el('div', {}, el(Btn, { onClick: () => openExternal(editHref), test: 'editor-link', ext: true }, 'Open in the WordPress editor')) : null)
}

function DeskDetail({ itemId, siteRow, go }) {
  const q = useDeskItem(itemId)
  const invalidate = useInvalidate()
  const [width, setWidth] = useState(null)
  const [linkResult, setLinkResult] = useState(null)
  const [linking, setLinking] = useState(null)
  const [asking, setAsking] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(null)
  const [problem, setProblem] = useState(null)
  const [actResult, setActResult] = useState(null)
  const [when, setWhen] = useState('')
  const job = useJob(() => invalidate())
  useEffect(() => {
    setLinkResult(null)
    setAsking(false)
    setNote('')
    setProblem(null)
    setActResult(null)
  }, [itemId])
  if (!itemId) return el('div', { className: 'wpb-panel wpb-detail wpb-muted', 'data-test': 'detail' }, 'Pick an item on the left.')
  const guard = guardData(q, 'This item')
  if (guard) return el('div', { className: 'wpb-panel wpb-detail', 'data-test': 'detail' }, guard)
  const { item, proof, seo, shots, publishing } = q.data
  const widths = Object.keys(shots || {}).map(Number).filter(Number.isFinite).sort((a, b) => b - a)
  const w = widths.includes(width) ? width : widths[0]
  const shot = (shots || {})[String(w)] || {}
  const before = safeImg(shot.before)
  const after = safeImg(shot.after)
  const diffRow = ((proof && proof.diff) || []).find(d => Number(d.width) === w)
  const isNew = item.kind === 'new_post' || item.kind === 'new_page' || item.kind === 'build_page'
  const checks = ((proof && proof.checks) || []).filter(c => c.width == null || Number(c.width) === w)
  const pub = publishing || { can_link: false, why: '' }
  // M3 adds `can_publish` (the site's level is Publish); a backend without it keeps v0.1's link only.
  const canPublish = item.can_publish === true || pub.can_publish === true
  const edit = item.links && item.links.edit
  const open = isOpen(item)
  const staged = item.kind === 'staged_edit' || item.kind === 'seo_fix'

  const reviewLink = async action => {
    setLinking(action)
    setLinkResult(null)
    try {
      const res = await rest('/desk/' + enc(item.id) + '/review-link?action=' + enc(action))
      if (res && res.ok && res.url) {
        openExternal(res.url)
        setLinkResult({ kind: 'opened', linkKind: res.kind })
        invalidate()
      } else setLinkResult({ kind: 'refused', message: (res && res.message) || 'The route gave no link.' })
    } catch (error) {
      setLinkResult({ kind: 'refused', message: (error.body && error.body.message) || error.message })
    } finally {
      setLinking(null)
    }
  }
  const act = async action => {
    setBusy(action)
    setProblem(null)
    setActResult(null)
    try {
      const body = action === 'schedule' ? { date: when } : {}
      const res = await rest('/desk/' + enc(item.id) + '/' + action, { method: 'POST', body })
      if (!res || res.ok === false) setActResult({ ok: false, message: (res && res.message) || 'WordPress did not take it.' })
      else setActResult({ ok: true, message: readBack(action, res) })
      invalidate()
    } catch (error) {
      const b = error.body || {}
      setActResult({ ok: false, message: b.message || error.message, level: /^level_/.test(String(b.error || '')), link: typeof b.review_link === 'string' ? b.review_link : (b.review_link && b.review_link.url) || null })
    } finally {
      setBusy(null)
    }
  }
  const markReviewed = async () => {
    setBusy('review')
    setProblem(null)
    try {
      const res = await rest('/desk/' + enc(item.id) + '/review', { method: 'POST', body: {} })
      if (res && res.ok === false) setProblem(res.message)
      invalidate()
    } catch (error) {
      setProblem(error.message)
    } finally {
      setBusy(null)
    }
  }
  const sendChanges = async () => {
    setBusy('changes')
    setProblem(null)
    try {
      const res = await rest('/desk/' + enc(item.id) + '/changes', { method: 'POST', body: { note: note.trim() } })
      if (!res || res.ok === false) {
        setProblem((res && res.message) || 'The note was not saved.')
        return
      }
      invalidate()
      setAsking(false)
      setNote('')
      await draftInChat(res.draft_text || 'Please change ' + item.title + ' on @wp:' + item.site + '/' + item.wp_id + ': ' + note.trim())
    } catch (error) {
      setProblem(error.message)
    } finally {
      setBusy(null)
    }
  }

  let primary
  if (canPublish && open) {
    primary = staged
      ? el(Btn, { kind: 'primary', onClick: () => act('apply'), disabled: Boolean(busy), test: 'apply' }, busy === 'apply' ? 'Applying…' : 'Apply to the live ' + (item.wp_type || 'post'))
      : el(Btn, { kind: 'primary', onClick: () => act('publish'), disabled: Boolean(busy), test: 'publish' }, busy === 'publish' ? 'Publishing…' : 'Publish now')
  } else if (pub.can_link && open) {
    const action = staged ? 'apply' : 'publish'
    primary = el(Btn, { kind: 'primary', onClick: () => reviewLink(action), disabled: Boolean(linking), test: 'review-link', title: 'Opens the review screen in wp-admin', ext: true }, linking ? 'Opening…' : 'Review in WordPress')
  } else {
    primary = el(Btn, { kind: 'primary', onClick: () => openExternal(edit), disabled: !edit, test: 'open-editor', ext: true }, 'Open in WordPress')
  }
  const siteLabel = siteRow ? siteRow.label : item.site
  const access = siteRow ? accessOf(siteRow) : null
  const pubWhy = !open ? '' : canPublish
    ? (staged ? 'Apply copies the staged change onto the live ' + (item.wp_type || 'post') + ' and removes the copy.' : 'This site\'s level is Publish: your click publishes at once.')
    : access && access.act === 'confirm' ? 'Confirm this site\'s access in Connections first.'
      : siteRow && siteRow.level && (siteRow.effective === 'lowered' ? siteRow.level_in_force : siteRow.level) !== 'publish' ? 'This site\'s Hermes user cannot publish. Your own login publishes in WordPress.'
        : pub.why || ''

  return el('div', { className: 'wpb-panel wpb-detail', 'data-test': 'detail', 'data-item': item.id },
    el('div', {},
      el('h2', { className: 'wpb-h2' }, item.title),
      el('div', { className: 'wpb-meta2' },
        el('span', { className: 'wpb-type' }, kindLabel(item)),
        el('span', {}, siteRow ? siteRow.label : item.site),
        el('span', {}, 'by ' + byWho(item.created_by) + ', ' + ago(item.created_at)),
        el('span', {}, 'WordPress: ' + (item.live_status || 'draft')),
        el('span', { className: cx('wpb-st', (STATE[item.state] || [])[1] && 'wpb-st-' + STATE[item.state][1]) }, (STATE[item.state] || [item.state])[0]))),
    el('div', { className: 'wpb-tools' },
      widths.length > 1 ? el(Seg, { label: 'Width', test: 'width-seg', value: w, onChange: setWidth, options: widths.map(x => ({ id: x, label: widthName(x), title: x + ' px' })) }) : null,
      el('span', { className: 'wpb-diffpct', 'data-test': 'diff' },
        isNew || !diffRow ? (isNew ? 'new ' + (item.wp_type || 'post') + ': nothing live to compare' : 'no diff for this width')
          : ['visual diff ', el('b', { key: 'b' }, diffRow.pct + '%'), ' of pixels at ' + w + ' px']),
      el('span', { style: { flex: 1 } }),
      el(Btn, { onClick: () => job.start('/jobs/proof', { item: item.id }), disabled: job.running, test: 'run-proof', icon: job.running ? 'loading' : 'debug-rerun' }, job.running ? 'Proof running…' : proof ? 'Run proof again' : 'Run proof')),
    job.problem ? el('div', { className: 'wpb-notice', 'data-test': 'job-problem', style: { marginBottom: 10 } }, job.problem) : null,
    job.job && job.job.state === 'failed' ? el('div', { className: 'wpb-notice wpb-bad', style: { marginBottom: 10 } }, 'The proof failed: ' + (job.job.error || 'no reason given')) : null,
    job.job && job.job.state === 'done' ? el('div', { className: 'wpb-notice wpb-info', 'data-test': 'job-done', style: { marginBottom: 10 } }, 'Proof finished' + (job.job.result && job.job.result.verdict ? ': ' + VERDICT[verdictOf(job.job.result.verdict)] : '') + '.') : null,
    el('div', { className: cx('wpb-shots', !before && 'wpb-single'), 'data-test': 'shots' },
      before ? el('div', { className: 'wpb-shot' },
        el('div', { className: 'wpb-cap' }, el('span', {}, 'Before: live'), el('span', {}, w + ' px')),
        el('div', { className: 'wpb-stage' }, el('div', { className: cx('wpb-frame', frameClass(w)) }, el('img', { src: before, alt: 'Live page at ' + w + ' px' })))) : null,
      el('div', { className: 'wpb-shot' },
        el('div', { className: 'wpb-cap' }, el('span', {}, before ? 'After: ' + (item.kind === 'staged_edit' ? 'staged' : 'draft') : isNew ? 'Draft (new, nothing live to compare)' : 'Draft'), el('span', {}, w ? w + ' px' : '')),
        el('div', { className: 'wpb-stage' }, after
          ? el('div', { className: cx('wpb-frame', frameClass(w)) }, el('img', { src: after, alt: item.title + ' at ' + w + ' px' }))
          : el('div', { className: 'wpb-noshot' }, 'No screenshot yet. Run the proof to take one at every width.')))),
    el('h3', { className: 'wpb-h3' }, 'Proof ', el('span', { className: 'wpb-xs wpb-muted', style: { fontWeight: 400 } }, proof ? 'real browser run, ' + w + ' px · ' + ago(proof.finished_at) + (proof.blocked_hosts && proof.blocked_hosts.length ? ' · blocked: ' + proof.blocked_hosts.join(', ') : '') : 'not run yet')),
    el('div', { className: 'wpb-proofs', style: { marginTop: 6 }, 'data-test': 'proof-chips' },
      proof ? checks.map((c, i) => el(ProofChip, { key: i, verdict: c.verdict, text: c.detail || checkLabel(c.check) })) : el('span', { className: 'wpb-small wpb-muted' }, 'No proof for this item yet.')),
    seo ? el('div', { style: { marginTop: 14 } },
      el('h3', { className: 'wpb-h3' }, 'SEO ', el('span', { className: 'wpb-xs wpb-muted', style: { fontWeight: 400 } }, seo.score + ' / 100')),
      el('div', { className: 'wpb-proofs', style: { marginTop: 6 } },
        (seo.checks || []).filter(c => c.verdict !== 'match').map((c, i) => el(ProofChip, { key: i, verdict: c.verdict, text: checkLabel(c.check) + ': ' + c.detail + (c.fix && c.fix.value ? ' → “' + c.fix.value + '”' : '') })))) : null,
    item.notes && item.notes.length ? el('div', { style: { marginTop: 14 } },
      el('h3', { className: 'wpb-h3' }, 'Notes'),
      item.notes.map((n, i) => el('div', { key: i, className: 'wpb-small', style: { marginTop: 4 } }, el('span', { className: 'wpb-muted' }, (n.by === 'hermes' ? 'Hermes' : 'You') + ', ' + ago(n.at) + ': '), n.text))) : null,
    el('div', { className: 'wpb-pubbox', 'data-test': 'pubbox' },
      el('div', { className: 'wpb-pubrow' },
        el('b', { className: 'wpb-small' }, 'Publishing on ' + siteLabel),
        access ? el(Badge, { tone: access.tone }, 'Access: ' + access.text) : null),
      pubWhy ? el('span', { className: 'wpb-xs wpb-muted', 'data-test': 'pub-why' }, pubWhy) : null,
      el('div', { className: 'wpb-actions' },
        primary,
        el(Btn, { onClick: () => setAsking(!asking), disabled: busy === 'changes', test: 'ask-changes' }, 'Ask for changes'),
        el(Btn, { onClick: markReviewed, disabled: busy === 'review' || item.state === 'reviewed', test: 'mark-reviewed' }, item.state === 'reviewed' ? 'Reviewed ✓' : busy === 'review' ? 'Saving…' : 'Mark reviewed')),
      canPublish && open && !staged ? el('div', { className: 'wpb-publine', 'data-test': 'schedule-box' },
        el('label', { className: 'wpb-xs wpb-muted', htmlFor: 'wpb-when-' + item.id }, 'Or schedule for'),
        el('input', { id: 'wpb-when-' + item.id, className: 'wpb-input', type: 'datetime-local', value: when, onChange: e => setWhen(e.target.value), 'data-test': 'schedule-date' }),
        el(Btn, { onClick: () => act('schedule'), disabled: Boolean(busy) || !when, test: 'schedule' }, busy === 'schedule' ? 'Scheduling…' : 'Schedule'),
        el('span', { className: 'wpb-xs wpb-muted' }, 'in the site\'s own time zone')) : null,
      actResult ? el('div', { className: cx('wpb-notice', actResult.ok ? 'wpb-info' : actResult.level ? '' : 'wpb-bad'), 'data-test': actResult.ok ? 'act-done' : 'act-refused' },
        el('span', {}, actResult.message),
        !actResult.ok && (actResult.link || edit) ? el('div', {}, el(Btn, { onClick: () => (actResult.link ? openExternal(actResult.link) : reviewLink(staged ? 'apply' : 'publish')), test: 'act-review-link', ext: true }, 'Review in WordPress')) : null) : null,
      asking ? el('div', { className: 'wpb-stack', style: { gap: 6 }, 'data-test': 'changes-box' },
        el('textarea', { className: 'wpb-textarea', value: note, onChange: e => setNote(e.target.value), placeholder: 'What should Hermes change? It opens in a chat for you to send.', 'aria-label': 'Note for Hermes' }),
        el('div', { className: 'wpb-actions' },
          el(Btn, { kind: 'primary', onClick: sendChanges, disabled: busy === 'changes', test: 'send-changes' }, busy === 'changes' ? 'Saving…' : 'Put it in a chat'),
          el(Btn, { kind: 'ghost', onClick: () => setAsking(false) }, 'Cancel'),
          el('span', { className: 'wpb-xs wpb-muted' }, 'Nothing is sent until you press send in the chat.'))) : null,
      problem ? el('div', { className: 'wpb-notice wpb-bad' }, problem) : null,
      el(ReviewLinkResult, { result: linkResult, editHref: edit })))
}

function SitePicker({ sites, value, onChange, allLabel }) {
  if (!sites || sites.length < 2) return null
  return el('select', { className: 'wpb-select', value: value || '', onChange: e => onChange(e.target.value), 'aria-label': 'Site', 'data-test': 'site-picker' },
    allLabel ? el('option', { value: '' }, allLabel) : null,
    sites.map(s => el('option', { key: s.id, value: s.id }, s.label + ' (' + host_(s.url) + ')')))
}

function Desk({ route, go, overview, many }) {
  const siteFilter = many ? route.site : ''
  const q = useDesk(siteFilter)
  const [filter, setFilter] = useState('all')
  const guard = guardData(q, 'The review desk')
  const rows = overview.sites || []
  const head = el('div', { className: 'wpb-lede' },
    el('h2', { className: 'wpb-h2' }, 'Review desk: only you sign off'),
    el('p', {}, 'Drafts, staged changes and SEO fixes with screenshots at every width and their proof.'),
    many ? el(SitePicker, { sites: rows, value: route.site, allLabel: 'All sites', onChange: site => go({ view: 'desk', site }) }) : null)
  if (guard) return el('div', {}, head, guard)
  const items = [...(q.data.items || [])].sort((a, b) => Number(isOpen(b)) - Number(isOpen(a)))
  const counts = q.data.counts || {}
  const visible = items.filter(i => filter === 'all' || kindGroup(i) === filter)
  const selected = visible.some(i => i.id === route.item) ? route.item : (visible[0] && visible[0].id) || ''
  const item = items.find(i => i.id === selected)
  return el('div', { 'data-test': 'desk' }, head,
    el('div', { className: 'wpb-desk' },
      el(Queue, { items, counts, filter, setFilter, selected, select: id => go({ view: 'desk', site: route.site, item: id }), many }),
      el(DeskDetail, { key: selected, itemId: selected, siteRow: item ? rows.find(r => r.id === item.site) : null, go })))
}

// ── SEO ──────────────────────────────────────────────────────────────────────────────────────────

function CheckRow({ c, act }) {
  const v = verdictOf(c.verdict)
  return el('div', { className: 'wpb-check', 'data-verdict': v },
    el('span', { className: 'wpb-s wpb-s-' + v }, VERDICT[v]),
    el('span', {}, el('b', {}, checkLabel(c.check)),
      el('span', { className: 'wpb-d' }, c.detail),
      c.fix && c.fix.value ? el('span', { className: 'wpb-d' }, 'Suggested: “' + c.fix.value + '”' + (c.fix.why ? ' (' + c.fix.why + ')' : '')) : null),
    el('span', { className: 'wpb-act' }, act || null))
}

function SeoView({ site, siteRow, sites, go }) {
  const q = useSeo(site)
  const desk = useDesk(site)
  const deskItems = (desk.data && desk.data.items) || []
  const invalidate = useInvalidate()
  const job = useJob(() => invalidate())
  const [post, setPost] = useState('')
  const picker = el(SitePicker, { sites, value: site, onChange: s => go({ view: 'seo', site: s }) })
  const head = el('div', { className: 'wpb-lede' }, el('h2', { className: 'wpb-h2' }, 'SEO without an SEO plugin'),
    el('p', {}, 'A check for every post before it reaches the desk, a site check, and fixes staged as drafts you approve.'), picker)
  const guard = guardData(q, 'The SEO reports')
  if (guard) return el('div', {}, head, guard)
  const reports = (q.data.post_reports || []).filter(r => !r.site || r.site === site)
  const titleOf = r => {
    if (r.title) return r.title
    const d = (deskItems || []).find(i => i.site === r.site && (i.wp_id === r.wp_id || i.source_id === r.wp_id || i.seo_id === r.id))
    return d ? d.title : 'Post ' + r.wp_id
  }
  const current = reports.find(r => String(r.wp_id) === post) || reports[0]
  const siteReport = q.data.site_report && (!q.data.site_report.site || q.data.site_report.site === site) ? q.data.site_report : null
  const fixAct = (c, r) => {
    if (!c.fix || c.verdict === 'match') return null
    const staged = (r.fixes || [])[0]
    if (staged) return el(Btn, { onClick: () => go({ view: 'desk', site: siteRow && sites.length > 1 ? site : '', item: staged }) }, 'On the desk')
    return el(Btn, { onClick: () => draftInChat('Stage the SEO fix "' + checkLabel(c.check) + '" for @wp:' + site + (r.wp_id ? '/' + r.wp_id : '') + ' as a draft: ' + c.fix.value), title: 'Fills a chat; you send it' }, 'Stage with Hermes')
  }
  const siteAct = c => {
    if (c.verdict === 'match') return null
    if (c.check === 'indexing' && siteRow) return el(Btn, { onClick: () => openExternal(siteRow.wp_admin_url + 'options-reading.php'), ext: true }, 'Open setting')
    return fixAct(c, siteReport)
  }
  return el('div', { 'data-test': 'seo' }, head,
    el('div', { className: 'wpb-grid2' },
      el('div', { className: 'wpb-stack' },
        el('div', { className: 'wpb-panel', 'data-test': 'post-check' },
          el('div', { className: 'wpb-phead' }, el('h3', { className: 'wpb-h3' }, 'Post check'),
            reports.length ? el('select', { className: 'wpb-select', value: current ? String(current.wp_id) : '', onChange: e => setPost(e.target.value), 'aria-label': 'Post', 'data-test': 'post-picker' },
              reports.map(r => el('option', { key: r.id, value: String(r.wp_id) }, titleOf(r) + ' · ' + r.score))) : null),
          current
            ? el('div', { className: 'wpb-checks' }, (current.checks || []).map((c, i) => el(CheckRow, { key: i, c, act: fixAct(c, current) })))
            : el('div', { className: 'wpb-side-note' }, 'No post checks yet. Every draft Hermes writes is checked before it reaches the desk.')),
        el('div', { className: 'wpb-panel', 'data-test': 'site-check' },
          el('div', { className: 'wpb-phead' }, el('h3', { className: 'wpb-h3' }, 'Site check'),
            siteReport ? el('span', { className: 'wpb-xs wpb-muted' }, ago(siteReport.created_at)) : null,
            el(Btn, { onClick: () => job.start('/jobs/seo-site', { site }), disabled: job.running, icon: job.running ? 'loading' : 'refresh', test: 'run-site-check' }, job.running ? 'Checking…' : 'Run site check')),
          job.problem ? el('div', { className: 'wpb-notice', style: { margin: '0 14px 10px' } }, job.problem) : null,
          siteReport
            ? el('div', { className: 'wpb-checks' }, (siteReport.checks || []).map((c, i) => el(CheckRow, { key: i, c, act: siteAct(c) })))
            : el('div', { className: 'wpb-side-note' }, 'No site check yet. It reads indexing, the sitemap, robots.txt, descriptions, mobile layout and speed.'))),
      el('aside', { className: 'wpb-panel wpb-side' },
        el('h2', { className: 'wpb-h2' }, 'Score'),
        el('div', { style: { padding: '0 14px 14px' } },
          el('span', { className: 'wpb-score', 'data-test': 'score' }, siteReport ? String(siteReport.score) : '—'), el('span', { className: 'wpb-muted' }, ' / 100'),
          el('p', { className: 'wpb-xs wpb-muted', style: { marginTop: 6 } }, 'A plain sum of the checks, explained per row. Not Google\'s number.')),
        el('h2', { className: 'wpb-h2' }, 'Search Console'),
        el('div', { className: 'wpb-side-note', style: { borderTop: 0 } }, 'Optional. Once connected, each post check shows the queries people find it with.'),
        el('div', { style: { padding: '0 14px 14px' } }, el(Btn, { onClick: () => go({ view: 'settings', site }) }, 'Connections in Settings')))))
}

// ── Build run (GeneratePress pack) ───────────────────────────────────────────────────────────────

function useSitePatch(site) {
  const invalidate = useInvalidate()
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState(null)
  const save = async changes => {
    setSaving(true)
    setResult(null)
    try {
      const res = await rest('/sites/' + enc(site), { method: 'PATCH', body: { changes } })
      if (!res || res.ok === false) setResult({ ok: false, message: (res && res.message) || 'Not saved.' })
      else setResult({ ok: true, message: res.changed === false ? 'Nothing changed.' : 'Saved' + (res.version ? ' as v' + res.version : '') + (res.diff && res.diff.length ? ': ' + res.diff.join('; ') : '.') })
      invalidate()
    } catch (error) {
      setResult({ ok: false, message: error.message })
    } finally {
      setSaving(false)
    }
  }
  return { save, saving, result }
}

function Step({ step, n, run, open, toggle }) {
  const invalidate = useInvalidate()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState(null)
  const gate = step.gate || {}
  const gateOpen = gate.who === 'you' && gate.state === 'open'
  const decide = async decision => {
    setBusy(true)
    setProblem(null)
    try {
      const res = await rest('/build/' + enc(run.id) + '/gate', { method: 'POST', body: { step: step.key, decision, note: '' } })
      if (res && res.ok === false) setProblem(res.message)
      invalidate()
    } catch (error) {
      setProblem(error.message)
    } finally {
      setBusy(false)
    }
  }
  const brand = run.brand || {}
  let content = null
  if (step.key === 'brief' && run.brief) content = el('span', {}, 'For ' + (run.brief.audience || 'the client') + '. Must have: ' + (run.brief.must_have || []).join(', ') + '. Tone: ' + (run.brief.tone || '—') + '.')
  if (step.key === 'brand' && brand.colors) {
    content = el('div', { className: 'wpb-stack', style: { gap: 6 } },
      el('div', { className: 'wpb-swatches' }, brand.colors.map(c => el('div', { key: c.slug, className: 'wpb-swatch' },
        el('i', { style: { background: /^#[0-9a-fA-F]{3,8}$/.test(c.color) ? c.color : 'transparent' } }),
        el('div', {}, c.name, el('br', {}), el('span', { className: 'wpb-mono wpb-xs wpb-muted' }, c.color))))),
      brand.fonts ? el('span', { className: 'wpb-muted' }, 'Fonts: ' + (brand.fonts.headings || '—') + ' for headings, ' + (brand.fonts.body || '—') + ' for text') : null)
  }
  if (step.key === 'sitemap' && run.sitemap) content = el('span', {}, run.sitemap.map(p => p.title).join(' · '))
  return el('div', { className: 'wpb-step wpb-step-' + step.state, 'data-test': 'step', 'data-step': step.key },
    el('span', { className: 'wpb-num' }, step.state === 'done' ? el(Codicon, { name: 'check', size: '0.85rem' }) : String(n)),
    el('div', { className: 'wpb-sbody' },
      el('button', { className: 'wpb-shead', onClick: toggle, type: 'button', 'aria-expanded': open },
        el('h3', { className: 'wpb-h3' }, step.label),
        (step.actors || []).map(a => el(Who, { key: a, who: a })),
        el('span', { className: 'wpb-xs wpb-muted wpb-mono' }, step.state)),
      open ? el('div', { className: 'wpb-scont' },
        content,
        el('div', { className: 'wpb-gate' },
          el('span', { className: 'wpb-g' }, 'gate'), el(Who, { who: gate.who }),
          el('span', { style: { flex: '1 1 160px' } }, gate.state === 'passed' ? 'passed' + (gate.note ? ': ' + gate.note : '') : gate.note || (gate.state === 'open' ? 'Waiting for your decision.' : 'Later.')),
          gateOpen ? el('span', { className: 'wpb-actions' },
            el(Btn, { kind: 'primary', onClick: () => decide('pass'), disabled: busy, test: 'gate-pass' }, 'Pass gate'),
            el(Btn, { onClick: () => decide('changes'), disabled: busy }, 'Ask for changes')) : null),
        problem ? el('div', { className: 'wpb-notice wpb-bad' }, problem) : null) : null))
}

function BuildRun({ run, siteRow }) {
  const firstOpen = (run.steps || []).findIndex(s => s.state !== 'done')
  const [open, setOpen] = useState(() => new Set([firstOpen]))
  const toggle = i => {
    const next = new Set(open)
    if (next.has(i)) next.delete(i)
    else next.add(i)
    setOpen(next)
  }
  const now = (run.steps || [])[firstOpen]
  return el('div', { className: 'wpb-grid2', 'data-test': 'build-run' },
    el('div', {}, (run.steps || []).map((s, i) => el(Step, { key: s.key, step: s, n: i + 1, run, open: open.has(i), toggle: () => toggle(i) }))),
    el('aside', { className: 'wpb-panel wpb-side' },
      el('h2', { className: 'wpb-h2' }, 'Run: ' + ((run.brief && run.brief.name) || run.id)),
      el('div', { style: { padding: '0 14px 12px' } },
        el('dl', { className: 'wpb-kv' },
          el('dt', {}, 'Site'), el('dd', {}, siteRow ? host_(siteRow.url) : run.site),
          el('dt', {}, 'Blocks'), el('dd', {}, run.mode === 'core' ? 'WordPress core blocks' : 'GenerateBlocks'),
          el('dt', {}, 'Pages'), el('dd', {}, String((run.pages || []).length) + ' on the desk'),
          el('dt', {}, 'Now'), el('dd', {}, now ? 'Step ' + (firstOpen + 1) + ', ' + now.label : 'handed off'),
          el('dt', {}, 'Updated'), el('dd', {}, ago(run.updated_at)))),
      el('div', { className: 'wpb-side-note' }, el(Who, { who: 'hermes' }), ' builds and proves · ', el(Who, { who: 'you' }), ' pass gates, publish · ', el(Who, { who: 'client' }), ' answers, edits later'),
      el('div', { className: 'wpb-side-note' }, 'Every page is plain blocks. The client edits it in the normal block editor; nothing depends on WPBrücke staying installed.')))
}

function BuildView({ site, siteRow, sites, go }) {
  const s = useSite(site)
  const packOn = Boolean(s.data && s.data.site && s.data.site.packs && s.data.site.packs.generatepress)
  const b = useBuilds(site, packOn)
  const patch = useSitePatch(site)
  const head = el('div', { className: 'wpb-lede' }, el('h2', { className: 'wpb-h2' }, 'Build run: from brief to handoff'),
    el('p', {}, 'Part of the optional GeneratePress pack. Works with GeneratePress and GenerateBlocks from wordpress.org.'),
    el(SitePicker, { sites, value: site, onChange: x => go({ view: 'build', site: x }) }))
  const guard = guardData(s, 'This site')
  if (guard) return el('div', {}, head, guard)
  if (!packOn) {
    return el('div', { 'data-test': 'build' }, head,
      el('div', { className: 'wpb-panel wpb-empty', 'data-test': 'pack-off' },
        el('b', {}, 'The GeneratePress pack is off for ' + (siteRow ? siteRow.label : site)),
        el('span', { className: 'wpb-muted wpb-small' }, 'Turn it on to build client sites as GenerateBlocks pages that clients keep editing in WordPress. Everything else in WPBrücke works without it.'),
        el(SwitchDisplay, { on: false, busy: patch.saving, label: 'GeneratePress pack', onClick: () => patch.save({ packs: { generatepress: true } }), test: 'pack-switch' }),
        patch.result ? el('span', { className: 'wpb-xs ' + (patch.result.ok ? 'wpb-muted' : '') }, patch.result.message) : null))
  }
  const runs = (b.data && b.data.runs) || []
  const run = [...runs].sort((x, y) => String(y.updated_at).localeCompare(String(x.updated_at)))[0]
  return el('div', { 'data-test': 'build' }, head,
    b.isLoading ? el(Loading, {}) : b.isError ? el(QueryProblem, { q: b, what: 'The build runs' })
      : run ? el(BuildRun, { run, siteRow })
        : el('div', { className: 'wpb-panel wpb-empty' }, el('b', {}, 'No build run yet'),
          el('span', { className: 'wpb-muted wpb-small' }, 'Start one in a chat with a short brief: who the site is for, what it must have, the tone.'),
          el(Btn, { kind: 'primary', onClick: () => draftInChat('Start a GeneratePress build run for @wp:' + site + '. Brief: ') }, 'Start a build run with Hermes')))
}

// ── Connections: Site N groups in Hermes' settings form (contract §§ 9 to 13) ───────────────────

const PAIRING_ONLY = ['no_user', 'no_password', 'host_changed', 'group_empty']
const CHECK_STATES = ['new', 'password_changed']
const SETTLED = ['confirmed', 'lowered', 'checking']

// Opening the settings form unmounts this page, so nothing about a connection lives in component
// state. What the page needs across a remount: which (group, password fingerprint, address, user) it
// already checked, in ctx.storage per Desktop (a failed check keeps the group `new`; this stops a
// remount from checking it again), and the checks in flight and the last Remove, in a module atom
// (the module outlives the page, so an answer that lands while the settings are open still counts).
const CHECKED_KEY = 'checked'
const LIVE_KEY = 'live'
const $conn = atom({ checked: {}, live: {}, running: {}, errors: {}, removed: [] })

function loadChecked(ctx) {
  try {
    const saved = ctx && ctx.storage ? ctx.storage.get(CHECKED_KEY, {}) : {}
    const live = ctx && ctx.storage ? ctx.storage.get(LIVE_KEY, {}) : {}
    $conn.set({ ...$conn.get(), checked: saved && typeof saved === 'object' ? saved : {}, live: live && typeof live === 'object' ? live : {} })
  } catch {
    /* no storage: the module atom alone remembers checks for this Desktop session */
  }
}

const patchConn = patch => $conn.set({ ...$conn.get(), ...patch })
const groupKey = (profile, n) => (profile || '-') + ':' + n
const checkKey = g => [g.fp || '', g.url || '', g.user || ''].join('|')
const useConn = () => useValue($conn)

function settingsHref(profile) {
  return SETTINGS_HREF + (profile ? '&profile=' + enc(profile) : '')
}

function useSettingsHref() {
  const profile = useProfile()
  const q = useGroups()
  return (q.data && q.data.settings_href) || settingsHref(profile)
}

// A group the page should check now: its address, user and password are there, and either nothing
// proved this password yet (new, password_changed) or a paired group has no check on file (a 0.1.0
// migration). Once per (group, fingerprint, address, user).
function wantsCheck(g, done) {
  if (!g || !g.url || g.error || !g.user || !g.set) return false
  if (CHECK_STATES.includes(g.state) || (g.state === 'paired' && !g.check)) return done !== checkKey(g)
  return false
}

// /overview reads the role live from WordPress; the stored check in /groups changes only on a check or
// Confirm. A paired group whose live state is stopped or lowered while its stored check says otherwise
// is checked again, once per (group, fingerprint, address, user, live state): the check brings the new
// level and caps_fp, so the card can say so and Confirm works. The card shows the live state meanwhile.
const LIVE_STATES = ['stopped', 'lowered']
const liveKey = (g, eff) => checkKey(g) + '|' + eff

function liveState(g, row) {
  const eff = row && row.effective
  if (!LIVE_STATES.includes(eff) || g.error || g.state !== 'paired') return null
  return g.check && g.check.state === eff ? null : eff
}

function wantsLive(g, row, done) {
  const eff = liveState(g, row)
  return Boolean(eff && g.url && g.user && g.set && done !== liveKey(g, eff))
}

const rowOf = (rows, g) => (rows || []).find(r => r.id === g.site_id) || null

function remember(key, map) {
  try {
    if (pluginCtx && pluginCtx.storage) pluginCtx.storage.set(key, map)
  } catch {
    /* kept in the atom only */
  }
}

async function runCheck(profile, g, after, liveEff) {
  const k = groupKey(profile, g.n)
  if ($conn.get().running[k]) return
  patchConn({ running: { ...$conn.get().running, [k]: true } })
  let error = null
  try {
    const res = await rest('/sites/check', { method: 'POST', body: { group: g.n }, timeoutMs: 60000 })
    if (!res || (res.ok === false && !res.state)) error = (res && res.message) || 'The check gave no answer.'
  } catch (e) {
    error = e.message
  }
  const now = $conn.get()
  const checked = { ...now.checked, [k]: checkKey(g) }
  remember(CHECKED_KEY, checked)
  const live = liveEff ? { ...now.live, [k]: liveKey(g, liveEff) } : now.live
  if (liveEff) remember(LIVE_KEY, live)
  const running = { ...now.running }
  delete running[k]
  const errors = { ...now.errors }
  if (error) errors[k] = error
  else delete errors[k]
  patchConn({ checked, live, running, errors })
  if (after) after()
}

// Runs from the page body, whatever view is open, so coming back from the settings checks at once.
function useAutoCheck(q, overview) {
  const profile = useProfile()
  const invalidate = useInvalidate()
  const data = q.data
  const rows = overview && overview.ok !== false ? overview.sites : null
  useEffect(() => {
    if (!data || data.ok === false) return
    const s = $conn.get()
    for (const g of data.groups || []) {
      const k = groupKey(profile, g.n)
      if (s.running[k]) continue
      const row = rowOf(rows, g)
      if (wantsCheck(g, s.checked[k])) runCheck(profile, g, invalidate)
      else if (wantsLive(g, row, s.live[k])) runCheck(profile, g, invalidate, liveState(g, row))
    }
  }, [data, rows, profile])
}

// What one group shows now, derived from /groups, its /overview row and the atom only.
function groupShown(g, conn, profile, row) {
  const k = groupKey(profile, g.n)
  const same = state => (g.check && g.check.state === state ? g.check : null)
  const live = liveState(g, row)
  if (conn.running[k]) return live ? { state: live, live: row, running: true } : { state: 'checking' }
  if (g.error) return { state: 'bad_address', check: same('bad_address') }
  if (PAIRING_ONLY.includes(g.state)) return { state: g.state, check: same(g.state) }
  if (wantsCheck(g, conn.checked[k])) return { state: 'checking' }
  if (live && wantsLive(g, row, conn.live[k])) return { state: live, live: row, running: true }
  if (conn.errors[k]) return live ? { state: live, live: row, error: conn.errors[k] } : { state: 'check_failed', error: conn.errors[k] }
  if (live) return { state: live, live: row }
  if (g.check) return { state: g.check.state, check: g.check }
  return { state: 'unchecked' }
}

const shownGroups = data => ((data && data.groups) || []).filter(g => g.url || g.site_id || g.error)

function needsPerson(data, conn, profile, rows) {
  const groups = shownGroups(data).filter(g => !SETTLED.includes(groupShown(g, conn, profile, rowOf(rows, g)).state))
  return groups.length + ((data && data.groups) || []).filter(g => g.migrate).length
}

const BADGES = {
  checking: ['Checking', null], needs_confirm: ['Unconfirmed', 'warn'], confirmed: ['Connected', 'good'], lowered: ['Lowered', 'warn'],
  stopped: ['Stopped', 'bad'], refused: ['Refused', 'bad'], group_empty: ['Half removed', 'warn'], unchecked: ['Not checked', null]
}

// The sentence for a pairing state when no check is on file yet (the same words as connect.py's STATES).
function localMessage(state, g) {
  const n = g.n
  if (state === 'no_user') return 'Site ' + n + ' has an address but no WordPress user. Fill Site ' + n + ' · WordPress user.'
  if (state === 'no_password') return 'Site ' + n + ' has no application password yet. Paste it into Site ' + n + ' · application password.'
  if (state === 'host_changed') return 'Site ' + n + ' now points to ' + (g.host || host_(g.url)) + '; its stored password belongs to another site. Paste the new site\'s password into Site ' + n + '.'
  if (state === 'group_empty') return 'Site ' + n + ' has no address any more; its site file ' + (g.site_id || '') + ' stays. Finish removing it, or fill Site ' + n + ' again.'
  if (state === 'bad_address') return 'Site ' + n + ' · address is not a web address (' + (g.error || 'unreadable') + ').'
  if (state === 'unchecked') return 'Site ' + n + ' is not checked yet.'
  return ''
}

// The decision for Remove, in the owner's words: "Page calls host.request('plugins.manage',
// settings) on his Remove click (public SDK door, same writer as the settings form); if it fails, the
// page says to empty the two fields by hand". No route writes Hermes' settings; this is the only write.
async function writeGroup(profile, n, url, user) {
  if (typeof host.request !== 'function') return { ok: false, why: 'This Desktop cannot write plugin settings from a page.' }
  try {
    const res = await host.request('plugins.manage', {
      action: 'settings',
      key: ID,
      values: { ['site' + n + '_url']: url, ['site' + n + '_user']: user },
      profile: profile || undefined
    })
    if (res && (res.ok === false || res.error)) return { ok: false, why: String(res.message || res.error) }
    return { ok: true }
  } catch (e) {
    return { ok: false, why: e && e.message ? e.message : String(e) }
  }
}

function migratedWords(m) {
  if (!m || typeof m !== 'object') return ''
  const parts = []
  if (m.never_publish_never_send !== undefined && m.never_publish_never_send !== null) parts.push('"never publish, never send" ' + (m.never_publish_never_send ? 'on' : 'off'))
  if (m.publishing) parts.push('publishing ' + m.publishing)
  if (m.agent_writes) parts.push('agent writes ' + m.agent_writes)
  return parts.length ? 'This level replaces the site\'s old settings (' + parts.join(', ') + ').' : ''
}

function Limit() {
  return el('p', { className: 'wpb-limit', 'data-test': 'limit-line' }, el(Codicon, { name: 'shield', size: '0.8rem' }), el('span', {}, LIMIT_LINE))
}

function ConfirmBlock({ g, check, again, after, recheck, extra }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const site = check.site_id || g.site_id
  const confirm = async () => {
    setBusy(true)
    setNote(null)
    try {
      const res = await rest('/sites/' + enc(site) + '/confirm', { method: 'POST', body: { level: check.level, caps_fp: check.caps_fp } })
      if (res && res.ok === false && !res.state) setNote({ bad: true, text: res.message || 'Not confirmed.' })
    } catch (e) {
      if (e.status === 409 && e.body && e.body.error === 'changed') setNote({ text: e.body.message || 'WordPress changed what this user can do. Read the new line and confirm that one.' })
      else setNote({ bad: true, text: (e.body && e.body.message) || e.message })
    } finally {
      setBusy(false)
      after()
    }
  }
  const migrated = migratedWords(check.migrated)
  return el('div', { className: 'wpb-stack', style: { gap: 9 }, 'data-test': 'confirm-card' },
    note ? el('div', { className: cx('wpb-notice', note.bad && 'wpb-bad'), 'data-test': note.bad ? 'confirm-error' : 'confirm-changed' }, note.text) : null,
    again
      ? el('p', { className: 'wpb-gmsg' }, check.message)
      : el('p', { className: 'wpb-confirm' }, 'Connected as ', el('b', {}, check.username || 'hermes'), ' at ', check.host || g.host, '. WordPress lets this user ', el('b', {}, check.level_words || LEVEL_NAME[check.level] || check.level), '. Hermes will do exactly that.'),
    migrated ? el('p', { className: 'wpb-xs wpb-muted' }, migrated) : null,
    again && check.level ? el('p', { className: 'wpb-xs wpb-muted' }, 'Confirm the new level, or narrow the role of ', el('b', {}, check.username || 'hermes'), ' in WordPress and check again.') : null,
    again && !check.level ? el('p', { className: 'wpb-xs wpb-muted', 'data-test': 'narrow-role' }, 'Narrow the role of ', el('b', {}, check.username || 'hermes'), ' in WordPress, then check again.') : null,
    el('div', { className: 'wpb-actions' },
      check.level && check.caps_fp && site
        ? el(Btn, { kind: 'primary', onClick: confirm, disabled: busy, test: 'confirm' }, busy ? 'Confirming…' : again ? 'Confirm again' : 'Confirm')
        : null,
      again && recheck ? el(Btn, { onClick: recheck, test: 'check-again' }, 'Check again') : null,
      extra || null),
    el(Limit, {}))
}

function RemovePanel({ g, siteId, label, profile, href, after, close }) {
  const keyed = g.state === 'paired' || Boolean(g.check && g.check.username)
  const [revoke, setRevoke] = useState(keyed)
  const [steps, setSteps] = useState([])
  const [busy, setBusy] = useState(false)
  const [manual, setManual] = useState(false)
  const n = g.n
  const put = (key, status, text) => setSteps(prev => {
    const at = prev.findIndex(s => s.key === key)
    const next = [...prev]
    if (at >= 0) next[at] = { key, status, text }
    else next.push({ key, status, text })
    return next
  })
  const done = list => {
    patchConn({ removed: [{ n, label, steps: list, at: Date.now() }, ...$conn.get().removed].slice(0, 3) })
  }
  const run = async () => {
    setBusy(true)
    setManual(false)
    const log_ = []
    const say = (key, status, text) => {
      put(key, status, text)
      const at = log_.findIndex(s => s.key === key)
      if (at >= 0) log_[at] = { key, status, text }
      else log_.push({ key, status, text })
    }
    // 1. revoke at WordPress (optional): what actually ends the password
    if (revoke) {
      say('revoke', 'run', 'Revoking the password in WordPress.')
      try {
        const r = await rest('/sites/' + enc(siteId) + '/revoke', { method: 'POST', body: {} })
        say('revoke', r && r.revoked ? 'ok' : 'fail', (r && r.message) || (r && r.revoked ? 'Revoked in WordPress.' : 'WordPress did not revoke it.'))
      } catch (e) {
        const missing = e.status === 404 && !(e.body && e.body.message && e.body.error !== 'not_found')
        say('revoke', 'fail', missing ? 'This WPBrücke cannot revoke yet. In WordPress: Users, hermes, Application Passwords, Revoke.' : (e.body && e.body.message) || e.message)
      }
    }
    // 2. empty the group through Desktop's own settings door
    say('empty', 'run', 'Emptying Site ' + n + ' · address and Site ' + n + ' · WordPress user.')
    const wrote = await writeGroup(profile, n, '', '')
    if (!wrote.ok) {
      say('empty', 'fail', 'Open the settings and empty Site ' + n + ' · address and Site ' + n + ' · WordPress user.')
      setManual(true)
      setBusy(false)
      after()
      return
    }
    say('empty', 'ok', 'Site ' + n + ' is empty. Its password stays stored but is never sent.')
    // 3. delete the site file once /groups shows the group without an address
    say('delete', 'run', 'Removing the site file.')
    let now = null
    try {
      const fresh = await rest('/groups')
      now = ((fresh && fresh.groups) || []).find(x => x.n === n) || null
    } catch {
      now = null
    }
    if (!now || now.url) {
      say('delete', 'fail', 'Site ' + n + ' still shows an address, so the site file stays. Finish removing once it is empty.')
    } else {
      try {
        await rest('/sites/' + enc(siteId), { method: 'DELETE' })
        say('delete', 'ok', 'Site file removed. Its saved versions stay in the history.')
        done(log_)
      } catch (e) {
        say('delete', 'fail', (e.body && e.body.message) || e.message)
      }
    }
    setBusy(false)
    after()
  }
  return el('div', { className: 'wpb-stack', style: { gap: 8 }, 'data-test': 'remove-panel' },
    el('b', { className: 'wpb-small' }, 'Remove ' + label + ' from Hermes'),
    keyed ? el('label', { className: 'wpb-check-box' },
      el('input', { type: 'checkbox', checked: revoke, onChange: e => setRevoke(e.target.checked), disabled: busy, 'data-test': 'revoke-box' }),
      el('span', {}, 'Revoke the password in WordPress')) : null,
    el('span', { className: 'wpb-xs wpb-muted' }, 'Hermes keeps the site file\'s saved versions. Nothing else changes on WordPress.'),
    steps.length ? el('div', { className: 'wpb-steps', 'data-test': 'remove-steps' },
      steps.map(s => el('div', { key: s.key, 'data-step': s.key, 'data-status': s.status },
        el('span', { className: 'wpb-mk wpb-mk-' + s.status }, { run: 'now', ok: 'done', fail: 'stop' }[s.status]), el('span', {}, s.text)))) : null,
    el('div', { className: 'wpb-actions' },
      el(Btn, { kind: 'primary', onClick: run, disabled: busy, test: 'remove-run' }, busy ? 'Removing…' : steps.length ? 'Try again' : 'Remove'),
      manual ? el(Btn, { onClick: () => host.navigate(href), test: 'remove-settings' }, 'Open the settings') : null,
      el(Btn, { kind: 'ghost', onClick: close, disabled: busy }, 'Cancel')))
}

function GroupCard({ g, row, profile, href }) {
  const conn = useConn()
  const invalidate = useInvalidate()
  const [removing, setRemoving] = useState(false)
  const [finishing, setFinishing] = useState(null)
  const shown = groupShown(g, conn, profile, row)
  const { state, check } = shown
  const [word, tone] = BADGES[state] || ['Needs you', 'warn']
  const named = g.host || host_(g.url) || (row && row.label) || g.site_id || ''
  const label = named || 'Site ' + g.n
  const siteId = (check && check.site_id) || g.site_id
  const recheck = () => runCheck(profile, g, invalidate)
  const finishRemove = async () => {
    setFinishing({ busy: true })
    try {
      await rest('/sites/' + enc(siteId), { method: 'DELETE' })
      patchConn({ removed: [{ n: g.n, label, steps: [{ key: 'delete', status: 'ok', text: 'Site file removed. Its saved versions stay in the history.' }], at: Date.now() }, ...$conn.get().removed].slice(0, 3) })
      setFinishing(null)
    } catch (e) {
      setFinishing({ error: (e.body && e.body.message) || e.message })
    }
    invalidate()
  }
  const settingsBtn = (text, kind) => el(Btn, { key: 'settings', kind: kind || 'primary', onClick: () => host.navigate((check && check.next && check.next.href) || href), test: 'open-settings' }, text || 'Open the settings')
  const againBtn = kind => el(Btn, { key: 'again', kind: kind || 'ghost', onClick: recheck, test: 'check-again' }, 'Check again')
  const removeBtn = siteId && !removing ? el(Btn, { key: 'remove', kind: 'ghost', onClick: () => setRemoving(true), test: 'remove' }, 'Remove') : null

  let body
  let actions = []
  if (state === 'checking') {
    body = el('p', { className: 'wpb-gmsg wpb-muted', 'data-test': 'checking' }, 'Checking ' + label + ' with the user and password in Site ' + g.n + '.')
  } else if (shown.live) {
    // The live state from /overview while the stored check still says otherwise (see liveState).
    const r = shown.live
    const who = (g.check && g.check.username) || g.user || 'hermes'
    const said = state === 'stopped'
      ? 'WPBrücke stopped ' + label + ': ' + (r.stop_reason || 'the user rights changed') + '.'
      : 'WordPress now lets ' + who + ' only ' + (r.level_words || 'less than you confirmed') + ' at ' + label + '; Hermes follows the narrower role.'
    const after = shown.running
      ? el('p', { key: 'run', className: 'wpb-xs wpb-muted', 'data-test': 'live-checking' }, 'Checking ' + label + ' again for what WordPress allows now.')
      : shown.error
        ? el('p', { key: 'err', className: 'wpb-xs wpb-muted' }, 'The check did not finish: ' + shown.error)
        : g.check && g.check.message ? el('p', { key: 'last', className: 'wpb-xs wpb-muted' }, 'Last check: ' + g.check.message) : null
    body = [el('p', { key: 'live', className: 'wpb-gmsg', 'data-test': 'live-state' }, said), after]
    if (!shown.running) actions = [againBtn(state === 'stopped' ? 'primary' : 'ghost')]
  } else if (state === 'needs_confirm') {
    body = el(ConfirmBlock, { key: 'confirm-' + g.n, g, check, after: invalidate, extra: removeBtn })
  } else if (state === 'stopped') {
    body = el(ConfirmBlock, { key: 'confirm-' + g.n, g, check, again: true, after: invalidate, recheck, extra: removeBtn })
  } else if (state === 'group_empty') {
    body = el('p', { className: 'wpb-gmsg' }, (check && check.message) || localMessage(state, g))
    actions = [
      el(Btn, { key: 'finish', kind: 'primary', onClick: finishRemove, disabled: Boolean(finishing && finishing.busy), test: 'finish-remove' }, 'Finish removing'),
      settingsBtn('Fill Site ' + g.n + ' again', 'default')
    ]
  } else if (state === 'check_failed') {
    body = el('p', { className: 'wpb-gmsg' }, 'The check did not finish: ' + shown.error)
    actions = [againBtn('primary')]
  } else if (check) {
    body = el('p', { className: 'wpb-gmsg' }, check.message)
    // The refusal says "one of the roles above", so the roles come first.
    if (state === 'refused') body = [el('p', { key: 'roles', className: 'wpb-xs wpb-muted' }, 'Roles that work: WPBrücke reader, Contributor, WPBrücke drafter, WPBrücke shop drafter, Author, WPBrücke shop publisher, Editor.'), el('p', { key: 'msg', className: 'wpb-gmsg' }, check.message)]
    const next = check.next || {}
    if (next.action === 'settings') actions = [settingsBtn(next.label), againBtn()]
    else if (next.action === 'check') actions = [againBtn('primary')]
    else if (next.action === 'open' && next.href) actions = [el(Btn, { key: 'open', kind: 'primary', onClick: () => openExternal(next.href), ext: true, test: 'open-next' }, next.label || 'Open')]
    else if (state === 'confirmed' || state === 'lowered') actions = [againBtn()]
  } else {
    body = el('p', { className: 'wpb-gmsg' }, localMessage(state, g))
    actions = state === 'unchecked' ? [againBtn('primary')] : [settingsBtn()]
  }
  if (removeBtn && !shown.running && (shown.live || !['group_empty', 'checking', 'needs_confirm', 'stopped'].includes(state))) actions.push(removeBtn)
  const cardTone = state === 'needs_confirm' ? 'confirm' : tone === 'bad' ? 'bad' : ''
  return el('article', { className: 'wpb-panel wpb-gcard', 'data-test': 'group', 'data-group': g.n, 'data-state': state, 'data-tone': cardTone, 'data-checking': shown.running ? '1' : undefined },
    el('div', { className: 'wpb-ghead' },
      el('span', { className: 'wpb-gn' }, 'Site ' + g.n),
      named ? el('b', {}, named) : null,
      g.user ? el('span', { className: 'wpb-xs wpb-muted' }, 'as ' + g.user) : null,
      el('span', { className: 'wpb-sp' }),
      el(Badge, { tone }, state === 'checking' || shown.running ? [el(Codicon, { key: 'i', name: 'loading', spinning: true, size: '0.7rem' }), word] : word)),
    body,
    finishing && finishing.error ? el('div', { className: 'wpb-notice wpb-bad' }, finishing.error) : null,
    actions.length ? el('div', { className: 'wpb-actions' }, actions) : null,
    removing && siteId ? el(RemovePanel, { g, siteId, label, profile, href, after: invalidate, close: () => setRemoving(false) }) : null)
}

function MigrateCard({ g, profile, href }) {
  const invalidate = useInvalidate()
  const [state, setState] = useState(null)
  const m = g.migrate || {}
  const fill = async () => {
    setState({ busy: true })
    const wrote = await writeGroup(profile, g.n, m.url || '', m.login || '')
    setState(wrote.ok ? null : { failed: true })
    invalidate()
  }
  return el('article', { className: 'wpb-panel wpb-gcard', 'data-test': 'migrate', 'data-group': g.n },
    el('div', { className: 'wpb-ghead' }, el('span', { className: 'wpb-gn' }, 'Site ' + g.n), el('b', {}, host_(m.url)), el('span', { className: 'wpb-sp' }), el(Badge, { tone: 'warn' }, 'From 0.1')),
    el('p', { className: 'wpb-gmsg' }, 'WPBrücke 0.1 connected ' + host_(m.url) + ' as ' + (m.login || 'its user') + '. Put both into Site ' + g.n + ' so it connects again; the password is already there.'),
    state && state.failed ? el('div', { className: 'wpb-notice', 'data-test': 'migrate-failed' },
      el('span', {}, 'Open the settings and fill Site ' + g.n + ' · address with ', el('code', { className: 'wpb-code' }, m.url || ''), ' and Site ' + g.n + ' · WordPress user with ', el('code', { className: 'wpb-code' }, m.login || ''), '.')) : null,
    el('div', { className: 'wpb-actions' },
      el(Btn, { kind: 'primary', onClick: fill, disabled: Boolean(state && state.busy), test: 'migrate-fill' }, 'Fill Site ' + g.n),
      state && state.failed ? el(Btn, { onClick: () => host.navigate(href), test: 'open-settings' }, 'Open the settings') : null))
}

const LEVEL_ROWS = [
  ['Read', 'WPBrücke reader (needs the WPBrücke connector)'],
  ['Drafts', 'Contributor (no images) or WPBrücke drafter (images, needs the connector)'],
  ['Publish', 'Author (its own posts) or Editor (all posts and pages)']
]

function Guide({ data, href, open, toggle, first }) {
  const n = data.free
  if (n === null || n === undefined) {
    return el('div', { className: 'wpb-panel wpb-guide', 'data-test': 'guide-full' },
      el('h2', { className: 'wpb-h2' }, 'Connect a site'),
      el('p', { className: 'wpb-muted' }, (data.used || 0) >= 16 ? 'All 16 site groups are in use. Remove a site to free one.' : 'No free site group right now. Bring back the 0.1 sites above first.'))
  }
  if (!open) {
    return el('div', { className: 'wpb-panel wpb-guide', 'data-test': 'guide-closed', style: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' } },
      el('span', {}, el('b', {}, 'Connect another site'), el('span', { className: 'wpb-muted' }, ' in Site ' + n + '.')),
      el(Btn, { onClick: toggle, test: 'guide-open', icon: 'add' }, 'Connect a site'))
  }
  return el('section', { className: 'wpb-panel wpb-guide', 'data-test': 'guide' },
    el('div', { className: 'wpb-ghead' },
      el('h2', { className: 'wpb-h2' }, 'Connect a site'),
      el(Badge, { tone: null }, 'Use Site ' + n),
      el('span', { className: 'wpb-sp' }),
      first ? null : el(Btn, { kind: 'ghost', onClick: toggle, test: 'guide-close' }, 'Close')),
    el('p', { className: 'wpb-muted', style: { maxWidth: '72ch' } }, 'Hermes connects to WordPress with its own user you need to create for it. Its role decides what Hermes can do.'),
    el('ol', {},
      el('li', {}, el('div', {},
        el('span', {}, 'In WordPress → Users → Add New User. There, create a user named ', el('b', {}, 'hermes'), ' with an email of its own and the role for the level you want.'),
        el('dl', { className: 'wpb-levels', 'data-test': 'levels' },
          LEVEL_ROWS.map(([lvl, role]) => [el('dt', { key: lvl + 't' }, lvl), el('dd', { key: lvl + 'd' }, role)]),
          el('dt', { className: 'wpb-no' }, 'Administrator'), el('dd', {}, 'Refused: too wide for Hermes.')))),
      el('li', {}, el('div', {},
        el('span', {}, 'In WordPress → Users → ', el('b', {}, 'hermes'), '. Under Application Passwords, add one named ', el('b', {}, 'Hermes'), ' and copy it.'))),
      el('li', {}, el('div', {},
        el('span', {}, 'Open the settings and fill ', el('b', {}, 'Site ' + n + ' · address'), ', ', el('b', {}, 'Site ' + n + ' · WordPress user'), ' and ', el('b', {}, 'Site ' + n + ' · application password'), '. Save, then come back here.'),
        el('div', { className: 'wpb-actions' }, el(Btn, { kind: 'primary', onClick: () => host.navigate(href), test: 'guide-settings' }, 'Open the settings'))))),
    el('p', { className: 'wpb-xs wpb-muted' }, 'Back here, WPBrücke checks the connection and asks you to confirm what Hermes can do.'))
}

function PublicSites({ rows, go }) {
  const invalidate = useInvalidate()
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)
  const add = async () => {
    setBusy('add')
    setMsg(null)
    try {
      const res = await rest('/public-sites', { method: 'POST', body: { url: url.trim() } })
      if (!res || res.ok === false) setMsg({ bad: true, text: (res && res.message) || 'Not added.' })
      else {
        setMsg({ text: 'Added ' + host_((res.site && res.site.url) || url) + '.' })
        setUrl('')
      }
    } catch (e) {
      setMsg({ bad: true, text: (e.body && e.body.message) || e.message })
    } finally {
      setBusy(null)
      invalidate()
    }
  }
  const remove = async id => {
    setBusy(id)
    setMsg(null)
    try {
      await rest('/sites/' + enc(id), { method: 'DELETE' })
      setMsg({ text: 'Removed ' + id + '. Its saved versions stay in the history.' })
    } catch (e) {
      setMsg({ bad: true, text: (e.body && e.body.message) || e.message })
    } finally {
      setBusy(null)
      invalidate()
    }
  }
  return el('aside', { className: 'wpb-panel wpb-side', 'data-test': 'public-sites' },
    el('h2', { className: 'wpb-h2' }, 'Public sites', el('span', { className: 'wpb-xs wpb-muted wpb-mono' }, String(rows.length))),
    el('div', { className: 'wpb-side-note', style: { borderTop: 0, paddingTop: 0 } }, 'No password: Hermes reads what the public sees, for the fleet and SEO.'),
    rows.map(r => el('div', { key: r.id, className: 'wpb-row', 'data-site': r.id },
      el('span', {}, el('b', {}, host_(r.url)), r.label && r.label !== host_(r.url) ? el('span', { className: 'wpb-xs wpb-muted' }, ' ' + r.label) : null),
      el('span', { className: 'wpb-actions' },
        el(Btn, { kind: 'ghost', onClick: () => go({ view: 'site', site: r.id }) }, 'Open'),
        el(Btn, { kind: 'ghost', onClick: () => remove(r.id), disabled: busy === r.id, test: 'public-remove' }, 'Remove')))),
    el('div', { className: 'wpb-side-note wpb-field' },
      el('label', { htmlFor: 'wpb-public-url' }, 'Add a public site'),
      el('div', { className: 'wpb-inline' },
        el('input', { id: 'wpb-public-url', className: 'wpb-input', type: 'url', value: url, onChange: e => setUrl(e.target.value), onKeyDown: e => { if (e.key === 'Enter' && url.trim()) add() }, placeholder: 'https://example.org', 'data-test': 'public-url' }),
        el(Btn, { onClick: add, disabled: busy === 'add' || !url.trim(), test: 'public-add' }, busy === 'add' ? 'Adding…' : 'Add')),
      msg ? el('span', { className: cx('wpb-xs', msg.bad ? '' : 'wpb-muted'), style: msg.bad ? { color: 'var(--wpb-bad)' } : null, 'data-test': 'public-msg' }, msg.text) : null))
}

function Removed() {
  const conn = useConn()
  if (!conn.removed.length) return null
  return el('div', { className: 'wpb-stack', style: { gap: 8 } },
    conn.removed.map(r => el('div', { key: r.at, className: 'wpb-notice wpb-info', 'data-test': 'removed' },
      el('span', {}, el('b', {}, 'Removed ' + r.label + '. '), r.steps.map(s => s.text).join(' ')),
      el('div', {}, el(Btn, { kind: 'ghost', onClick: () => patchConn({ removed: $conn.get().removed.filter(x => x.at !== r.at) }) }, 'Dismiss')))))
}

function ConnectView({ q, overview, go }) {
  const profile = useProfile()
  const [guideOpen, setGuideOpen] = useState(null)
  const head = el('div', { className: 'wpb-lede' },
    el('h2', { className: 'wpb-h2' }, 'Connections'),
    el('p', {}, 'Each WordPress site needs a dedicated user for Hermes. Its role decides what Hermes can do.'))
  const guard = guardData(q, 'Connections')
  if (guard) return el('div', { 'data-test': 'connect' }, head, guard)
  const data = q.data
  const href = data.settings_href || settingsHref(profile)
  const rows = (overview && overview.sites) || []
  const groups = shownGroups(data)
  const migrate = (data.groups || []).filter(g => g.migrate)
  const first = !groups.length && !migrate.length
  const open = guideOpen === null ? first : guideOpen
  const publicRows = rows.filter(r => !r.group || typeof r.group !== 'number')
  return el('div', { 'data-test': 'connect' }, head,
    el('div', { className: 'wpb-grid2' },
      el('div', { className: 'wpb-stack' },
        el(Removed, {}),
        migrate.map(g => el(MigrateCard, { key: 'm' + g.n, g, profile, href })),
        groups.map(g => el(GroupCard, { key: g.n, g, row: rows.find(r => r.id === g.site_id), profile, href })),
        el(Guide, { data, href, open, first, toggle: () => setGuideOpen(!open) })),
      el(PublicSites, { rows: publicRows, go })))
}

function ConnectNotice({ q, rows, go }) {
  const conn = useConn()
  const profile = useProfile()
  if (!q.data || q.data.ok === false) return null
  const n = needsPerson(q.data, conn, profile, rows)
  if (!n) return null
  return el('div', { className: 'wpb-notice', 'data-test': 'connect-notice', style: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 } },
    el('span', {}, n === 1 ? 'One site connection needs you.' : n + ' site connections need you.'),
    el(Btn, { onClick: () => go({ view: 'connect' }), test: 'connect-open' }, 'Open Connections'))
}

// ── Settings ─────────────────────────────────────────────────────────────────────────────────────

const GOOD_RIGHTS = ['can draft', 'can upload', 'cannot publish']

// One short value for a site-file field the page shows but does not edit (Hermes edits them in a chat).
function fieldWords(v) {
  if (v === null || v === undefined || v === '') return 'not set'
  if (Array.isArray(v)) return v.length ? v.map(fieldWords).join(', ') : 'none'
  if (typeof v === 'object') {
    const parts = Object.entries(v).filter(([, x]) => x !== null && x !== undefined && x !== '' && !(Array.isArray(x) && !x.length))
    return parts.length ? parts.map(([k, x]) => k.replace(/_/g, ' ') + ': ' + (typeof x === 'object' ? fieldWords(x) : String(x))).join('; ') : 'none'
  }
  if (typeof v === 'boolean') return v ? 'on' : 'off'
  return String(v)
}

function ConnectorBadge({ state, version }) {
  if (state === 'ok') return el(Badge, { tone: 'good' }, 'v' + version)
  if (state === 'older') return el(Badge, { tone: 'warn', title: 'This plugin ships v' + CONNECTOR_NEEDED }, 'v' + version + ' · older than needed')
  if (state === 'unread') return el(Badge, { title: 'Its version shows once the site has a password' }, 'installed')
  return el(Badge, { tone: 'warn' }, 'not installed')
}

// Desktop's gateway file download, or null on a Desktop without it.
const fileDownload = () => (typeof sdkNs.captureGatewayFileDownload === 'function' ? sdkNs.captureGatewayFileDownload : null)

// POST /connector-package writes the zip into the plugin's data folder on the Hermes host. The save is
// bound in the same call as that read (Desktop's lib/media.ts): it belongs to the connection and profile
// that answered, not to whichever one is focused when the person clicks.
function useConnectorPackage(enabled) {
  const profile = useProfile()
  const capture = fileDownload()
  return useQuery({
    queryKey: qk(profile, 'connector-package'),
    queryFn: async () => {
      const save = capture()
      const res = await rest('/connector-package', { method: 'POST', body: {}, timeoutMs: 30000 })
      return { ...res, save }
    },
    enabled: Boolean(enabled && capture),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    retry: retryOnce
  })
}

function cliSentence(why) {
  return el('span', { className: 'wpb-xs wpb-muted', 'data-test': 'connector-cli' },
    why + ': run ', el('code', { style: { whiteSpace: 'nowrap' } }, CONNECTOR_CLI), ' on the Hermes computer, then upload the zip in WordPress.')
}

// Download connector where Desktop can save the zip; the CLI sentence where it cannot (a Desktop
// without the SDK export, a profile whose workspace is an SSH machine, or a failed build).
function ConnectorGet({ siteUrl }) {
  const canSave = Boolean(fileDownload())
  const q = useConnectorPackage(true)
  const [saving, setSaving] = useState(false)
  const pkg = q.data && q.data.ok !== false ? q.data : null
  const install = el(InstallLink, { url: siteUrl })
  let body
  if (!canSave) body = cliSentence('This Desktop cannot save files from Hermes')
  else if (q.isError || (q.data && q.data.ok === false)) body = cliSentence('The zip could not be made here (' + ((q.error && q.error.message) || q.data.message) + ')')
  else if (pkg && pkg.workspace === 'ssh') body = cliSentence('This profile works on an SSH machine, so Desktop cannot fetch the zip')
  if (body) return el('div', { className: 'wpb-stack', style: { gap: 6 }, 'data-test': 'connector-get' }, body, install ? el('div', { className: 'wpb-actions' }, install) : null)
  const save = () => {
    if (!pkg || typeof pkg.save !== 'function') return
    setSaving(true)
    Promise.resolve(pkg.save(pkg.path, pkg.filename)).catch(() => {}).finally(() => setSaving(false))
  }
  return el('div', { className: 'wpb-stack', style: { gap: 6 }, 'data-test': 'connector-get' },
    el('div', { className: 'wpb-actions' },
      el(Btn, { kind: 'primary', onClick: save, disabled: !pkg || saving, test: 'download-connector', icon: 'cloud-download' }, 'Download connector'),
      install),
    el('span', { className: 'wpb-xs wpb-muted' }, pkg
      ? 'Connector ' + pkg.version + ' as ' + pkg.filename + '. In WordPress: Upload Plugin, choose the zip, Install Now, Activate.'
      : 'Preparing the zip.'))
}

function SettingsView({ site, siteRow, sites, go, compact }) {
  const q = useSite(site)
  const patch = useSitePatch(site)
  const href = useSettingsHref()
  const head = compact ? null : el('div', { className: 'wpb-lede' }, el('h2', { className: 'wpb-h2' }, 'Settings for this site'),
    el('p', {}, 'What Hermes can do here is the WordPress role you confirmed. Passwords stay in Hermes\' own settings form.'),
    el(SitePicker, { sites, value: site, onChange: x => go({ view: 'settings', site: x }) }))
  const guard = guardData(q, 'This site')
  if (guard) return el('div', {}, head, guard)
  const { site: s, facts, rights, not_available: dark, versions, yaml } = q.data
  const f = facts || {}
  const row = siteRow || {}
  const access = accessOf(row)
  const granted = s.access || null
  // The level in force: a lowered site runs at its narrower live level, not the one confirmed.
  const level = row.effective === 'lowered' ? row.level_in_force || null : row.level || (granted && granted.level) || null
  const publishLevel = level === 'publish' && row.effective !== 'stopped' && row.effective !== 'unconfirmed'
  const autos = Array.isArray(s.automations) ? s.automations : []
  const setPublish = (id, on) => patch.save({ automations: autos.map(a => (a.id === id ? { ...a, publish: on } : a)) })
  const connector = f.connector
  const cstate = connectorState(connector && connector.version, Boolean(connector || f.connector_ns))
  const groupN = typeof row.group === 'number' ? row.group : s.key_slot || 0
  const words = row.level_words || null
  const detail = [
    groupN ? 'Site ' + groupN : null,
    s.username || (f.user && f.user.username) ? 'user ' + (s.username || f.user.username) : null,
    granted && granted.role ? 'role ' + granted.role : null,
    granted && granted.confirmed_at ? 'confirmed ' + dayMonth(granted.confirmed_at) : null
  ].filter(Boolean).join(' · ')
  return el('div', { 'data-test': 'settings' }, head,
    el('div', { className: compact ? 'wpb-stack' : 'wpb-grid2' },
      el('div', { className: 'wpb-panel' },
        el('div', { className: 'wpb-set', 'data-test': 'access' }, el('div', { className: 'wpb-lbl' }, el('b', {}, 'Access'), el('span', {}, groupN ? 'From the WordPress role' : 'No password for this site')),
          el('div', { className: 'wpb-stack', style: { gap: 6 } },
            el('div', { className: 'wpb-rights' },
              el(Badge, { tone: access.tone }, access.text),
              (rights || []).map(r => el(Badge, { key: r, tone: GOOD_RIGHTS.includes(r) ? 'good' : null }, r))),
            words ? el('span', { className: 'wpb-small', 'data-test': 'level-words' }, 'Hermes can ' + words + '.') : null,
            row.effective === 'stopped' && row.stop_reason ? el('span', { className: 'wpb-small', style: { color: 'var(--wpb-bad)' }, 'data-test': 'stop-reason' }, 'Stopped: ' + row.stop_reason + '.') : null,
            detail ? el('span', { className: 'wpb-xs wpb-muted' }, detail) : null,
            el('div', { className: 'wpb-actions' },
              access.act === 'confirm' ? el(Btn, { kind: 'primary', onClick: () => go({ view: 'connect' }), test: 'confirm-again' }, row.effective !== 'stopped' ? 'Confirm' : row.restart_action === 'check' ? 'Check again' : 'Confirm again') : null,
              access.act === 'connect' ? el(Btn, { onClick: () => go({ view: 'connect' }), test: 'connect-site' }, 'Connect with a password') : null,
              groupN ? el(Btn, { kind: 'ghost', onClick: () => host.navigate(href), test: 'password-link' }, 'Change the password') : null))),
        el('div', { className: 'wpb-set', 'data-test': 'automations' }, el('div', { className: 'wpb-lbl' }, el('b', {}, 'Recurring drafts'), el('span', {}, 'Scheduled runs')),
          el('div', { className: 'wpb-stack', style: { gap: 8 } },
            autos.length
              ? autos.map(a => el('div', { key: a.id, className: 'wpb-stack', style: { gap: 3 } },
                el('span', { className: 'wpb-small' }, el('b', {}, a.id.replace(/-/g, ' ')), el('span', { className: 'wpb-muted' }, ' · ' + cronWords(a.schedule) + (a.enabled === false ? ' · paused' : ''))),
                publishLevel
                  ? el(SwitchDisplay, { on: Boolean(a.publish), busy: patch.saving, onClick: () => setPublish(a.id, !a.publish), label: a.publish ? 'Publishes its ' + (a.post_type || 'post') : 'Writes a draft; switch on to publish', test: 'auto-publish' })
                  : null))
              : el('span', { className: 'wpb-small wpb-muted' }, 'None yet. Ask Hermes for one in a chat.'),
            el('span', { className: 'wpb-xs wpb-muted' }, publishLevel ? 'A run publishes only when its switch is on.' : 'Runs write drafts. Publishing from a run needs the Publish level.'))),
        el('div', { className: 'wpb-set' }, el('div', { className: 'wpb-lbl' }, el('b', {}, 'Packs'), el('span', {}, 'Optional depth for one theme')),
          el('div', { className: 'wpb-stack', style: { gap: 6 } },
            el(SwitchDisplay, { on: Boolean(s.packs && s.packs.generatepress), busy: patch.saving, onClick: () => patch.save({ packs: { generatepress: !(s.packs && s.packs.generatepress) } }), label: 'GeneratePress pack: build runs, GenerateBlocks pages, colours and fonts', test: 'pack' }))),
        el('div', { className: 'wpb-set' }, el('div', { className: 'wpb-lbl' }, el('b', {}, 'Connections'), el('span', {}, 'All optional')),
          el('div', { className: 'wpb-stack wpb-small', style: { gap: 6 } },
            el('span', { 'data-test': 'connector-line' }, 'WPBrücke connector ', el(ConnectorBadge, { state: cstate, version: connector && connector.version }), ' ',
              el('span', { className: 'wpb-muted' }, 'reader and drafter roles, signed previews, SEO fields, the review screen')),
            cstate === 'missing' || cstate === 'older' ? el(ConnectorGet, { siteUrl: s.url }) : null,
            (dark || []).length ? el('div', { className: 'wpb-stack', style: { gap: 4 }, 'data-test': 'not-available' },
              el('span', { className: 'wpb-muted wpb-xs' }, 'Not available with this setup:'),
              dark.map(d => el('span', { key: d.feature, className: 'wpb-xs' }, el('b', {}, d.label), el('span', { className: 'wpb-muted' }, ': ' + d.because)))) : null)),
        el('div', { className: 'wpb-set', 'data-test': 'site-fields' }, el('div', { className: 'wpb-lbl' }, el('b', {}, 'Site file'), el('span', {}, 'Hermes changes these in a chat')),
          el('dl', { className: 'wpb-kv' },
            el('dt', {}, 'Label'), el('dd', {}, fieldWords(s.label)),
            el('dt', {}, 'Group'), el('dd', {}, fieldWords(typeof s.group === 'string' ? s.group : '')),
            el('dt', {}, 'Connect IP'), el('dd', {}, fieldWords(s.connect_ip)),
            el('dt', {}, 'Proof'), el('dd', {}, fieldWords(s.proof)),
            el('dt', {}, 'Memory'), el('dd', {}, fieldWords(s.memory)))),
        patch.result ? el('div', { className: 'wpb-set' }, el('span', {}), el('div', { className: cx('wpb-notice', patch.result.ok ? 'wpb-info' : 'wpb-bad'), 'data-test': 'patch-result' }, patch.result.message)) : null),
      el('aside', { className: 'wpb-panel wpb-side' },
        el('h2', { className: 'wpb-h2' }, 'Site file'),
        el('div', { className: 'wpb-side-note', style: { borderTop: 0 } }, 'One readable file per site. This page and Hermes both edit it; every save keeps a version with author and time.'),
        (versions || []).map(v => el('div', { key: v.version, className: 'wpb-ver' },
          el('span', { style: { minWidth: 0, overflowWrap: 'anywhere' } }, el('b', {}, 'v' + v.version), ' ' + (v.summary || '')),
          el('span', { className: 'wpb-xs wpb-muted' }, (v.author || '') + ', ' + ago(v.at)))),
        yaml ? el('pre', { className: 'wpb-yaml', 'data-test': 'yaml' }, yaml) : null)))
}

// ── the page ─────────────────────────────────────────────────────────────────────────────────────

function Tabs({ view, go, mode, waiting, packHint, route, connNeeds }) {
  const tabs = [
    ['home', mode === 'many' ? 'Fleet' : 'My site'],
    ['desk', 'Review desk', waiting ? String(waiting) : null],
    ['seo', 'SEO'],
    ['build', 'Build run', packHint],
    ['connect', 'Connections', connNeeds ? String(connNeeds) : null],
    ['settings', 'Settings']
  ]
  const active = view === 'site' ? 'home' : view
  return el('nav', { className: 'wpb-tabs', role: 'tablist', 'data-test': 'tabs' },
    tabs.map(([id, label, n]) => el('button', { key: id, className: 'wpb-tab', role: 'tab', 'aria-selected': active === id, onClick: () => go({ view: id, site: id === 'home' ? '' : route.site }), type: 'button', 'data-tab': id },
      label, n ? el('span', { className: id === 'build' ? 'wpb-xs wpb-muted' : 'wpb-n' }, n) : null)))
}

function Header({ q }) {
  const at = q.data && q.data.generated_at
  return el('div', { className: 'wpb-top' },
    el(Codicon, { name: 'globe', size: '1.1rem', style: { color: 'var(--wpb-muted)' } }),
    el('h1', { className: 'wpb-h1' }, 'WordPress'),
    el('span', { className: 'wpb-xs wpb-muted' }, 'WPBrücke'),
    el('span', { className: 'wpb-sp' }),
    at ? el('span', { className: 'wpb-xs wpb-muted' }, 'checked ' + ago(at)) : null,
    el(Btn, { kind: 'ghost', onClick: () => q.refetch(), disabled: q.isFetching, icon: q.isFetching ? 'loading' : 'refresh', test: 'refresh' }, 'Refresh'))
}

function NoSites() {
  return el(Problem, {
    test: 'no-sites',
    title: 'No WordPress site yet',
    text: 'Connect one on the WordPress page: a WordPress user for Hermes, its application password in the plugin settings, then one Confirm.',
    actions: [el(Btn, { key: 'c', kind: 'primary', onClick: () => host.navigate(PATH + '?view=connect') }, 'Open Connections')]
  })
}

function PageBody() {
  const q = useOverview()
  const gq = useGroups()
  useAutoCheck(gq, q.data)
  const conn = useConn()
  const profile = useProfile()
  const [route, go] = useRoute()
  const data = q.data
  useEffect(() => {
    if (data && data.ok !== false) showCount(data)
  }, [data && data.desk_waiting])
  let body
  const guard = guardData(q, 'WPBrücke')
  if (guard) body = guard
  else if (data.mode === 'none' || !(data.sites || []).length) body = el(ConnectView, { q: gq, overview: data, go })
  else {
    const mode = data.mode === 'many' ? 'many' : 'one'
    const sites = data.sites
    const rowOf = id => sites.find(s => s.id === id)
    const site = (route.site && rowOf(route.site) ? route.site : '') || sites[0].id
    const siteRow = rowOf(site)
    const needsFor = id => (data.needs_you || []).filter(n => !n.site || n.site === id)
    let view
    if (route.view === 'desk') view = el(Desk, { route, go, overview: data, many: mode === 'many' })
    else if (route.view === 'seo') view = el(SeoView, { site, siteRow, sites, go })
    else if (route.view === 'build') view = el(BuildView, { site, siteRow, sites, go })
    else if (route.view === 'connect') view = el(ConnectView, { q: gq, overview: data, go })
    else if (route.view === 'settings') view = el(SettingsView, { site, siteRow, sites, go })
    else if (mode === 'one' || route.view === 'site') view = el(SiteHome, { row: siteRow, needs: mode === 'one' ? data.needs_you || [] : needsFor(site), go, many: mode === 'many' })
    else view = el(Fleet, { data, go })
    const packHint = 'pack'
    const connNeeds = gq.data && gq.data.ok !== false ? needsPerson(gq.data, conn, profile, data.sites) : 0
    body = el('div', {},
      el(Tabs, { view: route.view, go, mode, waiting: data.desk_waiting, packHint, route, connNeeds }),
      route.view === 'connect' ? null : el(ConnectNotice, { q: gq, rows: data.sites, go }),
      view)
  }
  return el('div', { className: 'wpb wpb-scope', 'data-test': 'wpb-page' },
    el('div', { className: 'wpb-in' }, el(Header, { q }), body))
}

// A render error anywhere below shows a sentence and a button, never a white page.
class Guard extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  componentDidCatch(error) {
    log('render failed', String(error && error.message))
  }
  render() {
    if (this.state.error) {
      return el('div', { className: 'wpb-scope', style: { padding: 20 } },
        el(Problem, { title: 'This view hit an error', text: 'Something could not be drawn (' + String(this.state.error.message || this.state.error) + '). Nothing on your sites changed.', actions: [el(Btn, { key: 'r', onClick: () => this.setState({ error: null }) }, 'Draw it again')] }))
    }
    return this.props.children
  }
}

function WpbPage() {
  return el(Guard, {}, el(PageBody, {}))
}

// Settings ▸ Plugins ▸ WPBrücke: the same per-site settings next to Hermes' own form (which holds the keys).
function SettingsPageBody() {
  const q = useOverview()
  const [site, setSite] = useState('')
  const guard = guardData(q, 'WPBrücke')
  if (guard) return el('div', { className: 'wpb-scope', style: { padding: 4 } }, guard)
  const sites = (q.data && q.data.sites) || []
  if (!sites.length) return el('div', { className: 'wpb-scope' }, el(NoSites, {}))
  const id = site && sites.find(s => s.id === site) ? site : sites[0].id
  return el('div', { className: 'wpb-scope wpb', style: { height: 'auto' }, 'data-test': 'settings-page' },
    el('div', { className: 'wpb-stack' },
      el('div', { className: 'wpb-actions' },
        el('b', {}, 'Site'), sites.length > 1 ? el(SitePicker, { sites, value: id, onChange: setSite }) : el('span', {}, sites[0].label),
        el(Btn, { kind: 'ghost', onClick: () => host.navigate(PATH + '?view=settings&site=' + id) }, 'Open on the WordPress page')),
      el(SettingsView, { site: id, siteRow: sites.find(s => s.id === id), sites, go: r => host.navigate(routeHref(r)), compact: true })))
}

// Desktop loads this file from the computer it runs on, never from a remote Hermes, so an update on the
// server leaves this half behind. host.state.connectionId is 'local' for this computer's own Hermes; any
// other value, null or a Desktop without the field shows the line.
const NO_CONNECTION = atom(null)

function TwoPlaceNote() {
  const id = useValue((host.state && host.state.connectionId) || NO_CONNECTION)
  if (id === 'local') return null
  return el('div', { className: 'wpb-scope wpb-xs wpb-muted', 'data-test': 'two-place', style: { display: 'flex', alignItems: 'center', gap: 6, padding: '12px 0 0' } },
    el(Codicon, { name: 'info', size: '0.75rem' }),
    el('span', {}, 'Hermes on another computer? Update this plugin on both. Here: ',
      el('code', {}, 'hermes plugins update ' + ID), ', then Rescan.'))
}

// The Sidebar section and the two-place line sit outside the body's guard: they need no backend, so they
// show while the sites load or fail too.
function SettingsPage() {
  return el('div', {},
    el(Guard, {}, el(SettingsPageBody, {})),
    el(Guard, {}, el(TwoPlaceNote, {})),
    sidebarRow ? el(Guard, {}, el('div', { 'data-test': 'sidebar' }, jsx(SidebarSection, { row: sidebarRow, sdk: SIDEBAR_SDK }))) : null)
}

// ── transcript directive ::wp-draft{site="..." id="..."} ─────────────────────────────────────────

function WpDraftCard({ attrs, streaming }) {
  const site = String((attrs && attrs.site) || '')
  const id = String((attrs && attrs.id) || '')
  const valid = SITE_RE.test(site) && /^\d{1,12}$/.test(id)
  const profile = useProfile()
  const q = useQuery({ queryKey: qk(profile, 'desk', site), queryFn: () => rest('/desk?site=' + enc(site)), enabled: valid && !streaming, refetchInterval: POLL_MS, staleTime: 10000, retry: retryOnce })
  if (!valid) return el('span', { className: 'wpb-scope wpb-xs wpb-muted', 'data-test': 'wp-draft-invalid' }, 'wp-draft: needs site="<site id>" and id="<post number>"')
  const frame = (thumbNode, ...body) => el('div', { className: 'wpb-scope wpb-dcard', 'data-test': 'wp-draft' }, el('div', { className: 'wpb-th' }, thumbNode), el('div', { className: 'wpb-bd' }, ...body))
  const foot = el('span', { className: 'wpb-xs wpb-muted wpb-mono' }, 'wp-draft · ' + site + '/' + id)
  if (streaming || q.isLoading) return frame(el(Skel, { h: 90 }), el(Skel, { h: 16 }), el(Skel, { h: 22 }), foot)
  if (q.isError) return frame(null, el('b', {}, q.error.notOn ? 'WPBrücke is not on in this profile' : 'The draft could not be read'), el('span', { className: 'wpb-xs wpb-muted' }, q.error.notOn ? 'Turn the plugin on to see the card.' : q.error.message), foot)
  if (q.data && q.data.ok === false) return frame(null, el('b', {}, 'WPBrücke needs one more setting'), el('span', { className: 'wpb-xs wpb-muted' }, q.data.message), foot)
  const item = ((q.data && q.data.items) || []).find(i => String(i.wp_id) === id)
  if (!item) return frame(null, el('b', {}, 'Post ' + id + ' is not on the desk'), el('span', { className: 'wpb-xs wpb-muted' }, 'It may be published, discarded or on another site.'), el('div', { className: 'wpb-actions' }, el(Btn, { onClick: () => host.navigate(PATH + '?view=desk') }, 'Open the desk')), foot)
  const img = safeImg(item.thumb)
  const [word] = STATE[item.state] || [item.state]
  return frame(img ? el('img', { src: img, alt: item.title }) : null,
    el('div', { className: 'wpb-actions', style: { justifyContent: 'space-between' } }, el('b', {}, item.title), el(Badge, { tone: isOpen(item) ? null : 'good' }, kindLabel(item) + ' · ' + word)),
    (item.chips || []).length ? el('div', { className: 'wpb-proofs' }, item.chips.slice(0, 4).map((c, i) => el(ProofChip, { key: i, verdict: c.verdict, text: c.text }))) : null,
    el('div', { className: 'wpb-actions' },
      el(Btn, { kind: 'primary', onClick: () => host.navigate(routeHref({ view: 'desk', site, item: item.id })), test: 'open-on-desk' }, 'Open on the desk'),
      item.links && item.links.edit ? el(Btn, { onClick: () => openExternal(item.links.edit), ext: true }, 'Open in WordPress') : null),
    foot)
}

function WpDraftDirective(props) {
  return el(Guard, {}, el(WpDraftCard, props))
}

// ── registration ─────────────────────────────────────────────────────────────────────────────────

// sidebar-row v1 begin
// The plugin's sidebar row: its brand as the label (plus an optional count), a show switch and a
// position, kept per Desktop in ctx.storage under 'sidebar' as { show, position }. One source:
// templates/plugin-skeleton/desktop/sidebar-row.js. Every copy (desktop/src/sidebar-row.js in a
// built plugin, the marked block in a single-file desktop/plugin.js) must equal it between these
// markers; tests/test_sidebar_row.py fails one that differs. Change the template, then copy it.
// Import-free: the plugin passes the SDK names in `sdk`:
//   { SIDEBAR_NAV_AREA, Switch, Input, jsx, jsxs, atom, useValue }
const SIDEBAR_ROW_KEY = 'sidebar'
const SIDEBAR_ROW_MIN = 1
const SIDEBAR_ROW_MAX = 99

/** An integer from 1 to 99, or `fallback` when `value` is not a number. */
function sidebarRowPosition(value, fallback) {
  if (value === null || value === undefined || value === '') return fallback
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return fallback
  return Math.min(SIDEBAR_ROW_MAX, Math.max(SIDEBAR_ROW_MIN, n))
}

/** The row's label: the brand, plus " · N" while N is above 0 ("99+" from 100). */
function sidebarRowLabel(brand, count) {
  const n = Math.floor(Number(count) || 0)
  return n > 0 ? `${brand} · ${n < 100 ? n : '99+'}` : brand
}

/** Register the row from this Desktop's stored choice (nothing stored: shown, at defaultPosition).
 *  No backend read, so the row paints in its place with the first frame. Every change disposes the
 *  held contribution before it registers again: Desktop's registry replaces an entry by id, and an
 *  older disposer would then remove the new entry. */
function startSidebarRow(ctx, { brand, path, codicon, defaultPosition, sdk }) {
  const store = ctx.storage || { get: (key, fallback) => fallback, set: () => {} }
  const fallback = sidebarRowPosition(defaultPosition, 50)
  const stored = store.get(SIDEBAR_ROW_KEY, null)
  const saved = stored && typeof stored === 'object' ? stored : {}
  const $prefs = sdk.atom({ show: saved.show !== false, position: sidebarRowPosition(saved.position, fallback) })
  let count = 0
  let disposeRow = null
  let stopped = false

  const drop = () => {
    const dispose = disposeRow
    disposeRow = null
    if (dispose) dispose()
  }
  const apply = () => {
    drop()
    const { show, position } = $prefs.get()
    if (stopped || !show) return
    disposeRow = ctx.register({ id: 'nav', area: sdk.SIDEBAR_NAV_AREA, order: position, data: { codicon, label: sidebarRowLabel(brand, count), path } })
  }
  apply()

  return {
    brand,
    $prefs,
    prefs: () => $prefs.get(),
    /** A new count relabels a shown row; a hidden row stays hidden and shows the count when shown. */
    setCount(n) {
      const next = Math.max(0, Math.floor(Number(n) || 0))
      const relabel = sidebarRowLabel(brand, next) !== sidebarRowLabel(brand, count)
      count = next
      if (relabel && $prefs.get().show) apply()
    },
    /** Store { show, position } (either may be left out), then show, hide or move the row at once. */
    setPrefs(patch) {
      const now = $prefs.get()
      const p = patch && typeof patch === 'object' ? patch : {}
      const next = {
        show: 'show' in p ? p.show !== false : now.show,
        position: sidebarRowPosition(p.position, now.position),
      }
      if (next.show === now.show && next.position === now.position) return now
      store.set(SIDEBAR_ROW_KEY, next)
      $prefs.set(next)
      apply()
      return next
    },
    /** Remove the row for good (the plugin's own unload runs Desktop's disposers anyway). */
    dispose() {
      stopped = true
      drop()
    },
  }
}

/** The "Sidebar" block of the plugin's settings page: SDK Switch and Input, nothing else. */
function SidebarSection({ row, sdk }) {
  const { jsx, Switch, Input } = sdk
  const jsxs = sdk.jsxs || sdk.jsx
  const prefs = sdk.useValue(row.$prefs)
  const line = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 32 }
  const note = { margin: 0, fontSize: '0.75rem', color: 'var(--ui-text-secondary)' }
  const commit = event => {
    const next = row.setPrefs({ position: event.target.value })
    event.target.value = String(next.position)
  }
  return jsxs('section', {
    'data-sidebar-section': row.brand,
    style: { display: 'grid', gap: 6, margin: '12px 0', padding: '10px 12px', border: '1px solid var(--ui-stroke-tertiary)', borderRadius: 8 },
    children: [
      jsx('h3', { style: { margin: 0, fontSize: '0.8125rem', fontWeight: 600 }, children: 'Sidebar' }, 'title'),
      jsxs('div', { style: line, children: [
        jsx('span', { children: 'Show in the sidebar' }),
        jsx(Switch, { checked: prefs.show, 'aria-label': 'Show in the sidebar', onCheckedChange: on => row.setPrefs({ show: on }) }),
      ] }, 'show'),
      jsxs('div', { style: line, children: [
        jsx('span', { children: 'Position' }),
        jsx(Input, {
          type: 'number', min: SIDEBAR_ROW_MIN, max: SIDEBAR_ROW_MAX, step: 1, inputMode: 'numeric',
          'aria-label': 'Position', defaultValue: String(prefs.position), style: { width: 72 },
          onBlur: commit,
          onKeyDown: event => { if (event.key === 'Enter') commit(event) },
        }, String(prefs.position)),
      ] }, 'position'),
      jsx('p', { style: note, children: 'Lower numbers sit higher. Kanban is 50.' }, 'hint'),
      prefs.show ? null : jsx('p', { style: note, children: `Open it from the command palette: Open ${row.brand}.` }, 'hidden'),
    ],
  })
}
// sidebar-row v1 end

// Every SDK name the shared module uses, passed in because the block imports nothing.
const SIDEBAR_SDK = { SIDEBAR_NAV_AREA, Switch, Input, jsx, jsxs, atom, useValue }

// The row's count: the overview's desk_waiting. An answer without it (a refusal) leaves the last count.
export function waitingOf(overview) {
  if (!overview || overview.ok === false) return null
  const v = overview.desk_waiting
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : null
}

function showCount(overview) {
  const n = waitingOf(overview)
  if (sidebarRow && n !== null) sidebarRow.setCount(n)
}

let lastSites = []

async function pollNav() {
  try {
    const o = await rest('/overview')
    if (o && o.ok !== false) {
      lastSites = o.sites || []
      showCount(o)
    }
  } catch {
    /* the row keeps its last label; the page shows the error */
  }
}

async function newDraftCommand() {
  if (!lastSites.length) await pollNav()
  const site = lastSites.length === 1 ? lastSites[0].id : ''
  draftInChat('Write a blog draft for @wp:' + (site || '') + ' about ')
}

export default {
  id: ID,
  name: 'WPBrücke',
  description: 'Your WordPress sites: drafts by Hermes with proof at every width, SEO checks, and a review desk where you sign off.',
  register(ctx) {
    pluginCtx = ctx
    loadChecked(ctx)
    const removeStyle = installStyle()
    if (typeof ctx.onDispose === 'function') ctx.onDispose(removeStyle)
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => jsx(WpbPage, {}) })
    sidebarRow = startSidebarRow(ctx, { brand: 'WPBrücke', path: PATH, codicon: 'globe', defaultPosition: 56, sdk: SIDEBAR_SDK })
    ctx.register({
      id: 'open',
      area: PALETTE_AREA,
      data: { id: 'wpbruecke.open', label: 'Open WPBrücke', keywords: ['wordpress', 'wp', 'wpbruecke', 'sites'], run: () => host.navigate(PATH) }
    })
    ctx.register({
      id: 'open-desk',
      area: PALETTE_AREA,
      data: { id: 'wpbruecke.desk', label: 'WordPress: open review desk', keywords: ['wordpress', 'wp', 'review', 'drafts', 'desk'], run: () => host.navigate(PATH + '?view=desk') }
    })
    ctx.register({
      id: 'connect',
      area: PALETTE_AREA,
      data: { id: 'wpbruecke.connect', label: 'WordPress: connect a site', keywords: ['wordpress', 'wp', 'connect', 'password', 'site'], run: () => host.navigate(PATH + '?view=connect') }
    })
    ctx.register({
      id: 'new-draft',
      area: PALETTE_AREA,
      data: { id: 'wpbruecke.new-draft', label: 'WordPress: new blog draft', keywords: ['wordpress', 'wp', 'blog', 'post', 'draft'], run: () => newDraftCommand() }
    })
    ctx.register({ id: 'wp-draft', area: TRANSCRIPT_DIRECTIVE_AREA, data: { name: 'wp-draft', render: props => jsx(WpDraftDirective, props) } })
    if (typeof ctx.registerSettingsPage === 'function') {
      ctx.registerSettingsPage({ id: 'sites', title: 'WPBrücke', icon: 'globe', order: 50, render: () => jsx(SettingsPage, {}) })
    }
    pollNav()
    if (typeof ctx.setInterval === 'function') ctx.setInterval(pollNav, NAV_POLL_MS)
    log('registered', { path: PATH })
  }
}
