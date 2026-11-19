#!/usr/bin/env node
/* Score cards with the page's strict six-input model.
 *
 * Loads index.html, pulls out its inline script, runs it against a small DOM
 * stub and calls window.SCORE_RATED_CARD for each card. That function is the
 * same one the Today tab uses, so a re-scored result cannot drift from the
 * pick the page publishes. A card with any gap is returned as not rated with
 * the missing inputs named; it is never filled in.
 *
 * usage: node tooling/score_rated.js cards.json out.json [index.html]
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

if (typeof window.SCORE_RATED_CARD !== 'function') {
  console.error('index.html has no SCORE_RATED_CARD');
  process.exit(1);
}

const cards = JSON.parse(fs.readFileSync(inPath, 'utf8'));
const out = cards.map(c => {
  let got = null;
  try { got = window.SCORE_RATED_CARD(c); } catch (e) { got = null; }
  const base = {id: c.id || '', course: c.course, time: c.time, region: c.region || ''};
  if (!got) {
    const field = (c.runners || []).filter(r => !r.scratched).length;
    return Object.assign(base, {rated: false, pick: '', chance: null,
      missing: [{what: 'a factor did not score', n: field, of: field}]});
  }
  if (!got.rated) {
    return Object.assign(base, {rated: false, pick: '', chance: null,
      missing: got.missing || []});
  }
  return Object.assign(base, {
    rated: true, pick: got.pick, chance: got.chance,
    runners: got.runners || []
  });
});
fs.writeFileSync(outPath, JSON.stringify(out));
console.log('scored', out.length, 'cards,', out.filter(o => o.rated).length, 'rated');
