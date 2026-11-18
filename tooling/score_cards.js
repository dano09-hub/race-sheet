#!/usr/bin/env node
/* Score cards with the sheet's own model.
 * Loads index.html, pulls out its inline script, runs it against a small DOM
 * stub and calls window.SCORE_CARD for each card. This keeps settle.py on the
 * exact twelve-factor scoring the page publishes, with no second copy to drift.
 * The ordered model rows are included for the latest-day race audit.
 *
 * usage: node tooling/score_cards.js cards.json picks.json [index.html]
 */
const fs = require('fs');
const path = require('path');

const inPath = process.argv[2];
const outPath = process.argv[3];
const pagePath = process.argv[4] || path.join(__dirname, '..', 'index.html');

const els = {};
function mk(id){return {id,innerHTML:'',textContent:'',hidden:false,value:'',dataset:{},files:[],style:{},_l:{},
 classList:{set:new Set(),add(){},remove(){},contains(){return false;}},
 addEventListener(t,fn){this._l[t]=fn;},appendChild(){},removeChild(){},click(){},focus(){},dispatchEvent(){},
 setAttribute(k,v){(this.attrs=this.attrs||{})[k]=v;},getAttribute(k){return (this.attrs||{})[k];}};}
const panels=['today','card','card','card','ranked','results','method'].map(n=>{const e=mk('p-'+n);e.attrs={'data-panel':n};return e;});
const tabs=['today','card','ranked','results','method'].map(n=>{const e=mk('t-'+n);e.attrs={'data-tab':n};return e;});
global.document={getElementById:(id)=>(els[id]=els[id]||mk(id)),
 querySelectorAll:(s)=>s==='[data-panel]'?panels:(s==='[data-tab]'?tabs:[]),
 querySelector:()=>null,createElement:()=>mk('t'),body:{appendChild(){},removeChild(){}}};
global.window={confirm:()=>true,alert(){},print(){},scrollTo(){}};
global.localStorage={getItem:()=>null,setItem(){}};
global.Blob=function(){};global.URL={createObjectURL:()=>'',revokeObjectURL(){}};
global.FileReader=function(){};global.Event=function(t){this.type=t;};

const html = fs.readFileSync(pagePath, 'utf8');
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
blocks.sort((a, b) => b.length - a.length);
if (!blocks.length) { console.error('no inline script in ' + pagePath); process.exit(1); }
eval(blocks[0]);

const cards = JSON.parse(fs.readFileSync(inPath, 'utf8'));
const out = cards.map(c => {
  let info = null;
  try { info = window.SCORE_CARD(c); } catch (e) { info = null; }
  const rows = ((info && info.rows) || []).slice().sort((a, b) => b.total - a.total);
  const pick = rows.length ? (rows[0].r.horse || '') : '';
  // The page's own Model chance for the pick, as a percentage. Kept as null
  // when the page does not return a share, so settlement cannot invent one.
  const chance = rows.length && Number.isFinite(rows[0].share)
    ? Math.round(rows[0].share * 1000) / 10 : null;
  return {id: c.id || '', course: c.course, time: c.time, pick, chance,
          runners: rows.map((x, i) => ({
            horse: x.r.horse || '', rank: i + 1,
            total: Math.round(x.total * 10) / 10,
            mktRank: x.mktRank || null,
            odds: x.r.odds || '',
            // The chance and implied chance the page prints. Kept per runner so
            // a settled slip can be rebuilt with the same edge test the tab uses.
            share: Number.isFinite(x.share) ? Math.round(x.share * 1e6) / 1e6 : null,
            implied: Number.isFinite(x.implied) ? Math.round(x.implied * 1e6) / 1e6 : null,
            dec: window.DECIMAL_ODDS ? window.DECIMAL_ODDS(x.r.odds) : null,
            // The page's own 0-100 factor scores, so a training export fits
            // weights against exactly the inputs the model uses.
            parts: Object.fromEntries(Object.entries(x.parts || {}).map(
              ([k, v]) => [k, v === null ? null : Math.round(v * 10) / 10]))
          }))};
});
fs.writeFileSync(outPath, JSON.stringify(out));
console.log('scored', out.length, 'cards,', out.filter(o => o.pick).length, 'with a pick');
