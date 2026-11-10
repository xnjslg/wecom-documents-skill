function insertTable(options) {
  const e=window.pad.editor;
  if(!options.docid||location.pathname!=='/doc/'+options.docid)throw Error('Document differs');
  if(!e.clipboardManager.context.isEditable())throw Error('Document is not editable');
  const doc=e.service.getDocument(),selection=e.service.getSelection();
  const {rows,cols,cells}=options;
  if(!Number.isInteger(rows)||!Number.isInteger(cols)||rows<1||cols<1||!Array.isArray(cells)||cells.length!==rows||cells.some(r=>!Array.isArray(r)||r.length!==cols||r.some(c=>typeof c!=='string'||/[\x00-\x1f]/.test(c))))throw Error('Invalid cell matrix');
  const hits=doc.search(options.anchorText);
  if(hits.length!==1)throw Error('Anchor is absent or ambiguous');
  const anchor=hits[0].realRange;
  if(doc.readText(anchor.begin(),anchor.end()-anchor.begin())!==options.anchorText)throw Error('Anchor differs');
  const main=e.state.getStoryStream().getMainStoryInfo();
  if(!main||anchor.begin()<main.contentRange.begin()||anchor.end()>main.contentRange.end())throw Error('Anchor must be in the main story');
  const p=anchor.end();selection.setRange(p,p);
  if(selection.getTableSelectionService().getSelectedTable())throw Error('Nested table insertion is outside this template');
  if(selection.range().begin()!==p||selection.range().end()!==p||!e.state.getRestrictEdit().checkEditableByRange(selection.range()))throw Error('Insertion range differs or is not editable');
  const completed=[];let stage='insert-table';
  try {
    selection.getTableSelectionService().insertTable(rows,cols);
    completed.push({stage,start:p});
    const table=selection.getTableSelectionService().getSelectedTable();
    if(!table||table.rows.length!==rows||table.rows.some(r=>r.cells.length!==cols))throw Error('Inserted table dimensions differ');
    const writes=table.rows.flatMap((r,i)=>r.cells.map((c,j)=>({gcp:c.getContentRange().begin(),text:cells[i][j],row:i,col:j}))).sort((a,b)=>b.gcp-a.gcp);
    for(const w of writes) {
      stage='cell-text';
      doc.getRange(w.gcp,w.gcp).setText(w.text);
      completed.push({stage,row:w.row,col:w.col});
      if(options.header&&w.row===0) {
        stage='header-format';selection.setRange(w.gcp,w.gcp+w.text.length);
        const ts=selection.getTableSelectionService();
        if(w.text)selection.getFont().setBold(true);
        ts.setBackgroundColor('E8F0FE');ts.setVerticalAlign(2);
        completed.push({stage,row:w.row,col:w.col});
      }
    }
    return {status:'applied_needs_audit',completed,rows,cols};
  } catch(error) {
    return {status:'stopped_needs_audit',completed,failed:{stage,writeMayHaveOccurred:true,error:String(error)}};
  }
}
