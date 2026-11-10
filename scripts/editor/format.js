function formatDocument(options) {
  const e=window.pad.editor;
  if (!options.docid || location.pathname!=='/doc/'+options.docid) throw Error('Document differs');
  if (!e.clipboardManager.context.isEditable()) throw Error('Document is not editable');
  const doc=e.service.getDocument(), selection=e.service.getSelection();
  let sdk;window.webpackChunkmelo_dev.push([['codex-format-'+Date.now()],{},r=>{sdk=r(167864)}]);
  if (!sdk || !Array.isArray(options.operations) || !options.operations.length) throw Error('Missing SDK or operations');
  function locate(text,whole=false) {
    if (typeof text!=='string'||!text) throw Error('Expected nonempty target text');
    const found=doc.search(text);
    if (found.length!==1) throw Error('Target text is absent or ambiguous');
    const r=found[0].realRange,b=r.begin(),end=r.end();
    if (doc.readText(b,end-b)!==text) throw Error('Target text differs');
    if (whole) {
      const it=e.state.getTextStream().createParagraphIterator(new sdk.GCPRange(b,0));
      it.seek(b);const p=it.getRange();
      if(p.begin()!==b || p.end()!==end+1) throw Error('Target must be one complete paragraph without its terminator');
    }
    const range=new sdk.GCPRange(b,end-b);
    if (!e.state.getRestrictEdit().checkEditableByRange(range)) throw Error('Target range is not editable');
    return {b,end};
  }
  function target(op) {
    if(op.type==='list') {
      const first=locate(op.fromText,true),last=locate(op.toText,true);
      if(last.b<first.b) throw Error('List endpoints are reversed');
      const range=new sdk.GCPRange(first.b,last.end-first.b);
      const main=e.state.getStoryStream().getMainStoryInfo();
      if(!main||first.b<main.contentRange.begin()||last.end>main.contentRange.end())throw Error('Lists must remain in the main story');
      if(/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(doc.readText(first.b,last.end-first.b)))throw Error('List range contains a table or another structural object');
      if(!e.state.getRestrictEdit().checkEditableByRange(range))throw Error('List range is not editable');
      return {b:first.b,end:last.end};
    }
    return locate(op.text,op.type!=='font');
  }
  const allowed=['heading','font','paragraph','quote','list','task-state'];
  const expected=options.operations.map(op=>{
    if(!allowed.includes(op.type))throw Error('Unsupported operation');
    return target(op);
  });
  options.operations.forEach((op,i)=>{
    if(op.type!=='task-state')return;
    const r=expected[i],state=doc.getRange(r.b,r.end).getParagraphFormat().getTaskState();
    const created=options.operations.slice(0,i).some((previous,j)=>previous.type==='list'&&previous.kind==='task'&&expected[j].b<=r.b&&expected[j].end>=r.end);
    if(![1,2].includes(state)&&!created)throw Error('Task state requires an existing task or an earlier task-list operation');
  });
  const completed=[];
  for(let i=0;i<options.operations.length;i++) {
    const op=options.operations[i];
    let started=false;
    try {
      const r=target(op);
      if(r.b!==expected[i].b||r.end!==expected[i].end)throw Error('Target moved since preflight');
      selection.setRange(r.b,r.end);
      if(selection.range().begin()!==r.b||selection.range().end()!==r.end)throw Error('Selection differs');
      started=true;
      if(op.type==='heading')selection.setStyle(sdk.BuiltinStyleName['Heading'+op.level]);
      else if(op.type==='font') {
        const f=selection.getFont();
        for(const [key,method] of [['bold','setBold'],['italic','setItalic'],['strike','setStrike'],['color','setTextColor'],['shading','setShdColor'],['family','setFontFamily']])
          if(Object.prototype.hasOwnProperty.call(op,key))f[method](op[key]);
        if('underline' in op)f.setUnderline(sdk.STUnderline.single);
        if('sizePt' in op)f.setFontSize(Math.round(op.sizePt*20));
        if('verticalAlign' in op)f.setVerticalAlignRun(sdk.STVerticalAlignRun[op.verticalAlign]);
      } else if(op.type==='paragraph') {
        const p=selection.getParagraphFormat();
        if('alignment' in op)p.setParagraphAlignment(sdk.STJcWml.center);
        if('lineSpacing' in op)p.setLineSpacingRule(sdk.STLineSpacingRule.auto);
        for(const [key,method] of [['leftIndentTwips','setLeftIndent'],['spaceBeforeTwips','setSpaceBefore'],['spaceAfterTwips','setSpaceAfter'],['lineSpacing','setLineSpacing']])
          if(key in op)p[method](op[key]);
      } else if(op.type==='quote')selection.getParagraphFormat().setQuote(true);
      else if(op.type==='task-state') {
        const p=selection.getParagraphFormat();
        if(![1,2].includes(p.getTaskState()))throw Error('Target is no longer a task');
        p.setTaskState(op.state==='done'?2:1);
      }
      else {
        const style={numbered:{listFormat:1,listText:'%1.'},bullet:{listFormat:24,listText:sdk.BulletText.Circle},task:{listFormat:64,listText:sdk.TaskText.Todo}}[op.kind];
        const result=e.service.getNumbering().createListInRange(selection.range(),{styleset:[style]},true);
        if(!result)throw Error('List creation returned no result');
      }
      completed.push({index:i,type:op.type,start:r.b,end:r.end});
    } catch(error) {
      return {status:'stopped_needs_audit',completed,failed:{index:i,type:op.type,writeMayHaveOccurred:started,error:String(error)}};
    }
  }
  return {status:'applied_needs_audit',completed};
}
