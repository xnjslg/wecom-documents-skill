async function copyNativeMath(options) {
  const e=window.pad.editor, samples=[];
  if(!Array.isArray(options.ranges)||options.ranges.length<1||options.ranges.length>8)
    throw new Error('Provide 1..8 observed native formula ranges');
  for(const [index,range] of options.ranges.entries()){
    const {start,end}=range;
    if(!Number.isInteger(start)||!Number.isInteger(end)||end<=start)
      throw new Error('Invalid native formula range');
    const raw=e.service.getDocument().readText(start,end-start);
    if(raw.charCodeAt(0)!==22||raw.charCodeAt(raw.length-1)!==23||!e.state.getMathStream().getMathInfo(start,1))
      throw new Error('Range is not a complete observed native equation');
    e.service.getSelection().setRange(start,end);
    if(e.selection.gcpRange.begin()!==start||e.selection.gcpRange.end()!==end)
      throw new Error('Native copy selection differs');
    const copied=await e.clipboardManager.storeSelectionInnerData(e.selection.gcpRange,false);
    if(typeof copied.innerData!=='string')throw new Error('Native clipboard fragment missing');
    samples.push({index,start,end,innerData:copied.innerData,rawUtf16:Array.from({length:raw.length},(_,i)=>raw.charCodeAt(i))});
  }
  return {samples,version:window.App.collabCenterAdapter.getVersion()};
}
