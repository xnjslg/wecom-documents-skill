// Use the existing internal clipboard fragment; no system clipboard or UI.
async function replaceNativeMath(options) {
  const e=window.pad.editor, {start,end,source,innerData,rawUtf16}=options;
  const m=window.App.changesetManager,a=window.App.collabCenterAdapter;
  if(!e.clipboardManager.context.isEditable()||!e.clipboardManager.pasteInterface)
    throw new Error('Existing editable paste service is unavailable');
  if(!a.isConnected||m.hasPauseCollab||m.composeCommands.length||m.composeTimer!==null||a.getLocalUserChanges().length)
    throw new Error('Editor has pending changes');
  if(e.service.getDocument().readText(start,end-start)!==source)throw new Error('Source formula differs');
  if(typeof innerData!=='string'||rawUtf16[0]!==22||rawUtf16[rawUtf16.length-1]!==23)
    throw new Error('Native equation fragment is invalid');
  const selection=e.service.getSelection();selection.setRange(start,end);
  if(e.selection.gcpRange.begin()!==start||e.selection.gcpRange.end()!==end||
     !e.state.getRestrictEdit().checkEditableByRange(e.selection.gcpRange))throw new Error('Formula selection differs');
  selection.delete();selection.setRange(start,start);
  e.clipboardManager.pasteTemplate(innerData);
  for(let i=0;i<20;i++){
    await new Promise(resolve=>setTimeout(resolve,500));
    const raw=e.service.getDocument().readText(start,rawUtf16.length);
    const exact=raw.length===rawUtf16.length&&rawUtf16.every((unit,k)=>raw.charCodeAt(k)===unit);
    const info=e.state.getMathStream().getMathInfo(start,1);
    if(i>=1&&exact&&info&&a.isConnected&&!m.hasPauseCollab&&!m.composeCommands.length&&
       m.composeTimer===null&&!a.getLocalUserChanges().length)
      return {replaced:true,start,end:start+rawUtf16.length,nativeMath:true,version:a.getVersion()};
  }
  throw new Error('Native equation insertion is unconfirmed; inspect before any repeat');
}
