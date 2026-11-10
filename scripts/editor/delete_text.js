async function deleteExactText(options) {
  const e=window.pad.editor,m=window.App.changesetManager,a=window.App.collabCenterAdapter;
  const {start,end,source,after}=options;
  if(!Number.isInteger(start)||!Number.isInteger(end)||end<=start||typeof source!=='string'||typeof after!=='string'||!after.length)
    throw new Error('Provide exact source and following text for the selected range');
  if(!e.clipboardManager.context.isEditable()||e.service.getDocument().readText(start,end-start)!==source)
    throw new Error('Source text differs');
  if(!a.isConnected||m.hasPauseCollab||m.composeCommands.length||m.composeTimer!==null||a.getLocalUserChanges().length)
    throw new Error('Editor has pending changes');
  const selection=e.service.getSelection();selection.setRange(start,end);
  if(e.selection.gcpRange.begin()!==start||e.selection.gcpRange.end()!==end||
     !e.state.getRestrictEdit().checkEditableByRange(e.selection.gcpRange))throw new Error('Deletion selection differs');
  selection.delete();
  for(let i=0;i<20;i++){
    await new Promise(resolve=>setTimeout(resolve,500));
    if(i>=1&&e.service.getDocument().readText(start,after.length)===after&&a.isConnected&&!m.hasPauseCollab&&
       !m.composeCommands.length&&m.composeTimer===null&&!a.getLocalUserChanges().length)
      return {deleted:true,start,version:a.getVersion()};
  }
  throw new Error('Exact deletion is unconfirmed; inspect before any repeat');
}
