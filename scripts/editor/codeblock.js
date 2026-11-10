function editCodeblock(options) {
  const e=window.pad.editor;
  if(!options.docid||location.pathname!=='/doc/'+options.docid)throw Error('Document differs');
  if(!e.clipboardManager.context.isEditable())throw Error('Document is not editable');
  const doc=e.service.getDocument(),selection=e.service.getSelection();
  let sdk;window.webpackChunkmelo_dev.push([['codex-codeblock-'+Date.now()],{},r=>{sdk=r(167864)}]);
  if(!sdk||!['create','update'].includes(options.action)||typeof options.contentId!=='string'||!options.contentId)throw Error('Invalid action or content ID');
  const candidate=sdk.createCodeblockTextboxProperty({id:options.contentId,language:options.language??'plain text',theme:options.theme??'normal',wordWrap:options.wordWrap??true});
  if(!candidate)throw Error('Unsupported code language or theme');
  candidate.wordWrap=options.wordWrap??true;
  let range,prop;
  const story=e.state.getStoryStream().getSubStoryInfoFromId(options.contentId,7);
  if(options.action==='create') {
    if(story)throw Error('Content ID already exists; inspect before any retry');
    const hits=doc.search(options.anchorText);
    if(hits.length!==1)throw Error('Anchor is absent or ambiguous');
    const r=hits[0].realRange;
    if(doc.readText(r.begin(),r.end()-r.begin())!==options.anchorText)throw Error('Anchor differs');
    if(typeof options.code!=='string'||!options.code)throw Error('Expected nonempty code');
    const main=e.state.getStoryStream().getMainStoryInfo();
    if(!main||r.begin()<main.contentRange.begin()||r.end()>main.contentRange.end())throw Error('Anchor must be in the main story');
    selection.setRange(r.end(),r.end());
    if(selection.getTableSelectionService().getSelectedTable())throw Error('Codeblock insertion in a table is outside this template');
    range=new sdk.GCPRange(r.end(),0);
  } else {
    if(!Number.isInteger(options.gcp)||options.gcp<0||doc.readText(options.gcp,1).charCodeAt(0)!==8)throw Error('Drawing marker differs');
    const info=e.state.getDrawingStream().getDrawingInfoFromGcp(options.gcp);
    const id=info?.property?.drawing?.graphic?.graphicData?.wsp?.txbx?.contentId;
    if(id!==options.contentId||!story||!story.subStoryProp.isCodeBlock)throw Error('Codeblock identity differs');
    range=new sdk.GCPRange(options.gcp,1);
    prop=new(sdk.PropertyFactory.getPropertyClass('TextboxStoryProperty'));
    for(const key of ['language','theme','wordWrap'])if(Object.prototype.hasOwnProperty.call(options,key))prop[key]=options[key];
  }
  if(!e.state.getRestrictEdit().checkEditableByRange(range))throw Error('Target is not editable');
  let stage=options.action==='create'?'insert-codeblock':'update-codeblock';
  try {
    if(options.action==='create') {
      selection.setRange(range.begin(),range.end());
      if(selection.range().begin()!==range.begin()||selection.range().end()!==range.end())throw Error('Selection differs');
      const cb=doc.getCodeblocks();
      cb.addTextFrame({storyType:7,content:options.code,textboxStoryProperty:candidate},{textFrameProp:cb.createDefaultCodeblockProperty(options.contentId,doc),selection,updateSelection:true,spanStyle:'melo-codeblock-Base-theme-char',paraStyle:'melo-codeblock-Base-theme-para'});
    } else e.service.getTextframes().updateTextFrameSubStoryProperty(range,prop);
    return {status:'applied_needs_audit',action:options.action,contentId:options.contentId};
  } catch(error) {
    return {status:'stopped_needs_audit',contentId:options.contentId,failed:{stage,writeMayHaveOccurred:true,error:String(error)}};
  }
}
