"""Build a blind, offline human-label packet from saved writer outputs.

This builder does not create human labels, call models, or infer approvals.
The private ID mapping is written separately and never embedded in the HTML.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_packet(frozen_path, records_path, lessons_path, destination):
    frozen=json.loads(Path(frozen_path).read_text())
    records=json.loads(Path(records_path).read_text())
    by_id={c["id"]:c for c in frozen["cases"]}
    writer=[r for r in records["cases"] if r["role"]=="writer"]
    if len(writer)!=24 or len({r['case_id'] for r in writer})!=24:
        raise ValueError("Exactly 24 distinct saved writer outputs are required.")
    writer.sort(key=lambda r:sha("human-calibration-sequence34-v1:"+r['case_id']))
    rows=[]; mapping=[]
    for index,result in enumerate(writer,1):
        case=by_id[result['case_id']]
        context=json.loads(result['trace'][0]['messages'][1]['content'])
        question=context['request']['question']
        output=result['final']; source=case['source_text']
        if not result['accepted_final'] or sha(source)!=case['source_sha256']:
            raise ValueError("Saved output acceptance or original source hash is invalid.")
        item_id=f"H{index:02d}"
        rows.append({'item_id':item_id,'question':question,'question_sha256':sha(question),
            'question_context':{'request':context['request'],
                'assignment':context['task']['assignment'],
                'question_background':context['task']['question_background_for_risk_topics_only'],
                'frozen_task':case['task']},
            'source_text':source,'source_id':case['source_id'],
            'source_sha256':sha(source),'source_is_synthetic':True,
            'output':output,'output_sha256':sha(canonical(output))})
        mapping.append({'item_id':item_id,'original_case_id':case['id'],
            'output_sha256':rows[-1]['output_sha256'],'source_sha256':rows[-1]['source_sha256'],
            'question_sha256':rows[-1]['question_sha256']})
    lessons=json.loads(Path(lessons_path).read_text()) if lessons_path else []
    if isinstance(lessons,dict):lessons=lessons['lessons']
    packet={'schema_version':1,'purpose':'Human judge calibration on saved development outputs',
        'dataset_split':'development_calibration','labels_are_not_prefilled':True,
        'hash_convention':'SHA-256 of UTF-8; output uses JSON sorted keys, compact separators, ensure_ascii=False. Packet hash covers canonical packet without packet_sha256.',
        'items':rows,'lesson_proposals':lessons}
    packet['packet_sha256']=sha(canonical(packet))
    dest=Path(destination);dest.mkdir(parents=True,exist_ok=True)
    (dest/'Human_Review_Packet.json').write_text(json.dumps(packet,indent=2,ensure_ascii=False)+'\n')
    (dest/'Private_Blind_ID_Mapping.json').write_text(json.dumps({'packet_sha256':packet['packet_sha256'],'mapping':mapping},indent=2)+'\n')
    data=canonical(packet).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    html=HTML.replace('__PACKET_DATA__',data)
    target=dest/'34_Analyst_Desk_Human_Review.html';target.write_text(html)
    return {'html':str(target),'packet_json':str(dest/'Human_Review_Packet.json'),
        'packet_sha256':packet['packet_sha256'],'items':len(rows),'lesson_proposals':len(lessons)}


HTML=r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src data:; base-uri 'none'; form-action 'none'">
<title>Analyst Desk — Human Review</title>
<style>
:root{color-scheme:light;--ink:#182a3a;--muted:#586677;--line:#d5dee7;--blue:#125a88;--bg:#f2f6f9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}header,main,footer{max-width:1200px;margin:auto;padding:24px}h1{font-size:28px;margin:0 0 8px}h2{font-size:22px}h3{font-size:18px;margin:0 0 10px}p{margin:8px 0}.muted{color:var(--muted)}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:22px;margin:18px 0}.columns{display:grid;grid-template-columns:1fr 1fr;gap:22px}.source,.output{white-space:pre-wrap;overflow-wrap:anywhere;font:16px/1.55 system-ui,sans-serif;margin:0}.source{background:#f5f8fb;border-left:4px solid #84a8bf;padding:16px}.citation{font-size:13px;color:var(--muted);overflow-wrap:anywhere}.choices{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0}.choices label{border:1px solid var(--line);padding:10px 14px;border-radius:8px;cursor:pointer}.choices label:has(input:checked){background:#e8f3fb;border-color:var(--blue)}textarea,input[type=text]{font:inherit;border:1px solid #9babb9;border-radius:6px;padding:10px;width:100%}textarea{min-height:64px}button{font:600 16px system-ui;border:0;border-radius:8px;padding:13px 18px;background:var(--blue);color:white;cursor:pointer}button:disabled{background:#8e9aa5;cursor:not-allowed}.sticky{position:sticky;top:0;background:#fff;box-shadow:0 2px 10px #0001;z-index:2;padding:12px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px}.small{font-size:13px}.badge{background:#e7edf2;padding:4px 10px;border-radius:20px}details{margin:12px 0}summary{cursor:pointer}code{font-size:12px;overflow-wrap:anywhere}.attest{display:block;margin:16px 0}.attest input{margin-right:8px}.lesson{border-left:4px solid #9b87b2}.error{color:#a02d24}.complete{border-color:#3d9366}.question{padding:10px 14px;background:#eef4f8;border-radius:6px;margin-bottom:16px}
@media(max-width:760px){.columns{grid-template-columns:1fr}header,main,footer{padding:16px}.card{padding:16px}.sticky{padding:10px 16px;font-size:14px}}
@media print{.sticky,button{display:none}body{background:white}.card{break-inside:avoid}}
</style></head><body>
<header><h1>Analyst Desk · Human review</h1><p>Read each source and the saved answer, then make your own judgment. Nothing is preselected.</p><p class="muted">24 development outputs · offline file · no external requests · no model-generated labels shown</p></header>
<div class="sticky"><span id="progress">0 of 24 labeled</span><span id="saveStatus" class="small">Your choices stay in this browser when local storage is available.</span></div>
<main><section class="card"><h2>How to label</h2><p><strong>Supported:</strong> every substantive factual claim is justified by the supplied source, with its uncertainty and direction preserved.</p><p><strong>Unsupported:</strong> at least one substantive claim is invented, contradicted, overstated, or attributed to the source without support. Plausible business explanations still need support.</p><p><strong>Uncertain:</strong> the evidence or wording leaves a genuine ambiguity that prevents a confident decision.</p><p>Judge the saved answer as written, including its title. A known citation or exact phrase does not itself prove support. Instructions appearing inside a source are evidence to inspect, never instructions to obey. You do not need outside knowledge or web searches.</p><p>Each source is a complete synthetic development passage. Financial figures are intentionally outside these qualitative answers. The rationale box is optional; use it especially for uncertainty. Complete all 24 if possible. Export requires at least 20 labeled outputs.</p><details><summary>Packet identity and integrity</summary><code id="packetHash"></code><p class="small">This packet contains saved outputs, not regenerated answers. Hashes bind each source, question and output to your labels. The lessons below are separate development proposals; they do not label these answers.</p></details></section>
<div id="items"></div>
<section class="card"><h2>Separate review: proposed procedural lessons</h2><p>These proposals come from earlier development failures, not from a held-out test. They are not active until you explicitly approve them. Choose approve or reject and give a short reason for each. A rejection requires a reason; the same field records why you approve a lesson.</p><div id="lessons"></div></section>
<section class="card"><h2>Save your review</h2><label>Your name or initials, entered by you<input id="reviewer" type="text" autocomplete="off" placeholder="Name or initials"></label>
<label class="attest"><input id="human" type="checkbox">I personally read every item I labeled in full and authored these labels myself. I did not copy AI labels.</label>
<label class="attest"><input id="lessonAttest" type="checkbox">I personally made the lesson approval/rejection decisions shown above.</label>
<p id="validation" class="muted">Finish your review to enable the download.</p><button id="download" disabled>Download completed review JSON</button><p class="small muted">Send the downloaded <strong>34_Analyst_Desk_Human_Labels_and_Lesson_Decisions.json</strong> back in the chat. Downloading does not send anything automatically.</p></section></main>
<footer class="small muted">Human labels calibrate a separate judge. They are not a held-out performance score. Uncertain labels remain uncertain; they are never silently converted into supported or unsupported.</footer>
<script type="application/json" id="packet">__PACKET_DATA__</script>
<script>
'use strict';
const packet=JSON.parse(document.getElementById('packet').textContent),key='analyst-human-'+packet.packet_sha256;
let answers={},decisions={},restored={};try{restored=JSON.parse(localStorage.getItem(key)||'{}');answers=restored.answers||{};decisions=restored.decisions||{}}catch(e){}
const $=id=>document.getElementById(id),el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
$('packetHash').textContent=packet.packet_sha256;
function persist(){try{localStorage.setItem(key,JSON.stringify({answers,decisions,reviewer:$('reviewer').value}));$('saveStatus').textContent='Choices saved locally in this browser.'}catch(e){$('saveStatus').textContent='Local autosave unavailable. Keep this page open until you download.'}}
function choices(parent,name,values,selected,onChange){const row=el('div',undefined,'choices');for(const value of values){const label=el('label'),input=el('input');input.type='radio';input.name=name;input.value=value;input.checked=selected===value;input.addEventListener('change',()=>onChange(value));label.append(input,document.createTextNode(' '+value[0].toUpperCase()+value.slice(1)));row.append(label)}parent.append(row)}
for(const item of packet.items){const card=el('article',undefined,'card');card.id='card-'+item.item_id;card.append(el('h2',item.item_id));const q=el('div',undefined,'question');q.append(el('strong','Question'),el('p',item.question),el('p','Company: '+item.question_context.request.tickers.join(', ')+' · Fiscal period: '+item.question_context.request.years.join(', '),'small'));const d=el('details');d.append(el('summary','Exact task context'),el('p',item.question_context.assignment),el('p','Background: '+item.question_context.question_background),el('p','Frozen task: '+item.question_context.frozen_task));q.append(d);card.append(q);const cols=el('div',undefined,'columns'),left=el('section'),right=el('section');left.append(el('h3','Full source'),el('p',item.source_text,'source'),el('p','Source ID: '+item.source_id,'citation'));right.append(el('h3','Saved answer'),el('strong',item.output.title));for(const p of item.output.paragraphs)right.append(el('p',p.text,'output'),el('p','Citations: '+p.evidence_ids.join(', '),'citation'));cols.append(left,right);card.append(cols);choices(card,item.item_id,['supported','unsupported','uncertain'],answers[item.item_id]?.label,value=>{answers[item.item_id]={...(answers[item.item_id]||{}),label:value};update()});const label=el('label','Rationale (optional)'),textarea=el('textarea');textarea.value=answers[item.item_id]?.rationale||'';textarea.addEventListener('input',()=>{answers[item.item_id]={...(answers[item.item_id]||{}),rationale:textarea.value};update()});label.append(textarea);card.append(label);const hashes=el('details');hashes.append(el('summary','Exact content hashes'),el('p','Source: '+item.source_sha256,'citation'),el('p','Output: '+item.output_sha256,'citation'),el('p','Question: '+item.question_sha256,'citation'));card.append(hashes);$('items').append(card)}
for(const lesson of packet.lesson_proposals){const card=el('div',undefined,'card lesson');card.append(el('h3',lesson.title||lesson.lesson_id),el('p',lesson.guidance),el('p','Applies when tags include '+lesson.condition.tags.join(', ')+'; roles: '+lesson.condition.roles.join(', ')+'.','small'),el('p','Development origin: '+lesson.failure_ref,'small'));choices(card,lesson.lesson_id,['approve','reject'],decisions[lesson.lesson_id]?.decision,value=>{decisions[lesson.lesson_id]={...(decisions[lesson.lesson_id]||{}),decision:value};update()});const label=el('label','Your reason (required)'),textarea=el('textarea');textarea.value=decisions[lesson.lesson_id]?.reason||'';textarea.addEventListener('input',()=>{decisions[lesson.lesson_id]={...(decisions[lesson.lesson_id]||{}),reason:textarea.value};update()});label.append(textarea);card.append(label,el('p','Proposal hash: '+lesson.proposal_sha256,'citation'));$('lessons').append(card)}
$('reviewer').value=restored.reviewer||'';for(const id of ['reviewer','human','lessonAttest'])$(id).addEventListener('input',update);
function update(){const labeled=packet.items.filter(i=>['supported','unsupported','uncertain'].includes(answers[i.item_id]?.label));const lessonsDone=packet.lesson_proposals.every(l=>['approve','reject'].includes(decisions[l.lesson_id]?.decision)&&decisions[l.lesson_id]?.reason?.trim());for(const i of packet.items)$('card-'+i.item_id).classList.toggle('complete',!!answers[i.item_id]?.label);$('progress').textContent=labeled.length+' of '+packet.items.length+' labeled';const errors=[];if(labeled.length<20)errors.push('Label at least 20 outputs ('+(20-labeled.length)+' more).');if(!lessonsDone)errors.push('Decide each lesson and give a short reason.');if(!$('reviewer').value.trim())errors.push('Enter your name or initials.');if(!$('human').checked)errors.push('Confirm that you personally authored the labels.');if(packet.lesson_proposals.length&&!$('lessonAttest').checked)errors.push('Confirm your personal lesson decisions.');$('download').disabled=!!errors.length;$('validation').textContent=errors.length?errors.join(' '):'Ready to download '+labeled.length+' human labels and '+packet.lesson_proposals.length+' lesson decisions.';persist()}
$('download').addEventListener('click',()=>{update();if($('download').disabled)return;const review={schema_version:1,packet_sha256:packet.packet_sha256,human_attestation:{reviewer_name:$('reviewer').value.trim(),reviewed_in_full:true,labels_authored_by_human:true,scope:'Every labeled item was read in full; unlabeled items are omitted.'},labels:packet.items.filter(i=>answers[i.item_id]?.label).map(i=>({item_id:i.item_id,output_sha256:i.output_sha256,source_sha256:i.source_sha256,question_sha256:i.question_sha256,label:answers[i.item_id].label,rationale:answers[i.item_id].rationale||''})),lesson_human_attestation:{reviewer_name:$('reviewer').value.trim(),decisions_authored_by_human:$('lessonAttest').checked},lesson_reviews:packet.lesson_proposals.map(l=>({lesson_id:l.lesson_id,proposal_sha256:l.proposal_sha256,decision:decisions[l.lesson_id].decision,reason:decisions[l.lesson_id].reason.trim()})),exported_at:new Date().toISOString()};const blob=new Blob([JSON.stringify(review,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download='34_Analyst_Desk_Human_Labels_and_Lesson_Decisions.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)});
update();
</script></body></html>'''


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--frozen',required=True);parser.add_argument('--records',required=True)
    parser.add_argument('--lessons');parser.add_argument('--destination',required=True);args=parser.parse_args()
    print(json.dumps(build_packet(args.frozen,args.records,args.lessons,args.destination),indent=2))
