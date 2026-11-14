#!/usr/bin/env node
/* Score cards with the sheet's own model.
 * Loads index.html, pulls out its inline script, runs it against a small DOM
 * stub and calls window.PICK_FOR for each card. This keeps settle.py on the
 * exact twelve-factor scoring the page publishes, with no second copy to drift.
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
  let pick = '';
  try { pick = window.PICK_FOR(c) || ''; } catch (e) { pick = ''; }
  return {id: c.id || '', course: c.course, time: c.time, pick};
});
fs.writeFileSync(outPath, JSON.stringify(out));
console.log('scored', out.length, 'cards,', out.filter(o => o.pick).length, 'with a pick');
