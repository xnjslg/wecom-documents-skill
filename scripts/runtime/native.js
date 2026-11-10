'use strict';

if (Process.mainModule.name.toLowerCase() !== 'wecom-agent.exe') {
  throw new Error('Attach only to the installed WeCom agent');
}
const baseUrl = 'https://in-claw.work.weixin.qq.com/cgi-bin/claw';
const operations = {
  discovery: {path:'/service/discovery', keys:['service']},
  search_groups: {path:'/chat/groups/search', keys:['keywords','only_match_name','limit','cursor']},
  group_members: {path:'/chat/get', keys:['chat_id']},
  user_profiles: {path:'/contact/users/get', keys:['userids']},
  search_users: {path:'/contact/users/search', keys:['keywords','only_bot','search_mode']},
  search_files: {path:'/chat/files/search', keys:['chat_ids','begin_time','end_time','keywords','sender_userids','file_types','cursor','limit']},
  search_messages: {path:'/message/search', keys:['chat_ids','chat_types','begin_time','end_time','keywords','sender_userids','cursor','include_bot_msg']}
};

async function post(request) {
  const connection = await Socket.connect({family:'ipv4',host:'127.0.0.1',port:9882});
  const timer = setTimeout(() => connection.close(),45000);
  try {
    const body = unescape(encodeURIComponent(JSON.stringify(request)));
    const text = 'POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:9882\r\nContent-Type: application/json\r\nAccept: application/json\r\nContent-Length: '+body.length+'\r\nConnection: close\r\n\r\n'+body;
    await connection.output.writeAll(Uint8Array.from(text,c=>c.charCodeAt(0)).buffer);
    let response='';
    for (;;) {
      const chunk=await connection.input.read(65536);
      if(!chunk.byteLength)break;
      const bytes=new Uint8Array(chunk);
      for(let offset=0;offset<bytes.length;offset+=8192) {
        response+=String.fromCharCode(...bytes.subarray(offset,offset+8192));
      }
      if(response.length>8*1024*1024)throw new Error('Response exceeded 8 MiB');
      const split=response.indexOf('\r\n\r\n');
      if(split>=0) {
        const headers=response.slice(0,split);
        const length=/content-length:\s*(\d+)/i.exec(headers);
        if(length&&response.length>=split+4+Number(length[1]))break;
        if(/transfer-encoding:\s*chunked/i.test(headers)&&response.endsWith('\r\n0\r\n\r\n'))break;
      }
    }
    return response;
  } finally {
    clearTimeout(timer);
    await connection.close();
  }
}

rpc.exports = {
  identity() {
    return {pid:Process.id,path:Process.mainModule.path};
  },
  async capabilities() {
    return await post({jsonrpc:'2.0',id:1,method:'tools/list',params:{}});
  },
  async downloadavatar(url,output) {
    if(typeof url!=='string'||url.length>512||!/^https:\/\/wework\.qpic\.cn\/[^\s#]*$/.test(url)||typeof output!=='string'||!output.trim())throw new Error('Expected verified avatar URL and output path');
    return await post({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'download_file',arguments:{url:url,output:output}}});
  },
  async downloadmedia(mediaId,output) {
    if(typeof mediaId!=='string'||!mediaId.trim()||mediaId.length>1024||typeof output!=='string'||!output.trim())throw new Error('Expected media ID and output path');
    return await post({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'download_media',arguments:{media_id:mediaId,output:output}}});
  },
  async query(operation,parameters,traceId) {
    const spec=operations[operation];
    if(!Object.prototype.hasOwnProperty.call(operations,operation))throw new Error('Unsupported read operation');
    if(!parameters||typeof parameters!=='object'||Array.isArray(parameters))throw new Error('Parameters must be an object');
    if(Object.keys(parameters).some(k=>!spec.keys.includes(k)))throw new Error('Unexpected parameter');
    if(operation==='search_users'&&(!Array.isArray(parameters.keywords)||parameters.keywords.length<1||parameters.keywords.length>10||
        parameters.keywords.some(k=>typeof k!=='string'||k.length<1||k.length>2048)||
        typeof parameters.only_bot!=='boolean'||parameters.search_mode!=='list'))throw new Error('Invalid contact identity search parameters');
    if(operation==='search_files'&&(!Array.isArray(parameters.chat_ids)||parameters.chat_ids.length!==1||
        typeof parameters.chat_ids[0]!=='string'||!parameters.chat_ids[0].trim()||
        typeof parameters.begin_time!=='string'||!parameters.begin_time||typeof parameters.end_time!=='string'||!parameters.end_time||
        typeof parameters.cursor!=='string'||!Number.isInteger(parameters.limit)||parameters.limit<1||parameters.limit>200||
        (parameters.keywords!==undefined&&(!Array.isArray(parameters.keywords)||parameters.keywords.length<1||parameters.keywords.length>10||parameters.keywords.some(k=>typeof k!=='string'||!k.trim()||k.length>128)))||
        (parameters.sender_userids!==undefined&&(!Array.isArray(parameters.sender_userids)||parameters.sender_userids.length<1||parameters.sender_userids.length>50||parameters.sender_userids.some(k=>typeof k!=='string'||!k.trim())))||
        (parameters.file_types!==undefined&&(!Array.isArray(parameters.file_types)||parameters.file_types.length<1||parameters.file_types.some(k=>!['doc','excel','ppt','zip','voice','exe','other'].includes(k))))))throw new Error('Invalid scoped file search parameters');
    if(operation==='search_messages'&&(!Array.isArray(parameters.chat_ids)||parameters.chat_ids.length!==1||
        typeof parameters.chat_ids[0]!=='string'||!parameters.chat_ids[0].trim()||
        !Array.isArray(parameters.chat_types)||parameters.chat_types.length!==1||parameters.chat_types[0]!=='group'||
        typeof parameters.begin_time!=='string'||!parameters.begin_time||typeof parameters.end_time!=='string'||!parameters.end_time||
        typeof parameters.cursor!=='string'||typeof parameters.include_bot_msg!=='boolean'||
        (parameters.keywords!==undefined&&(!Array.isArray(parameters.keywords)||parameters.keywords.length<1||parameters.keywords.length>10||
          parameters.keywords.some(k=>typeof k!=='string'||!k.trim()||k.length>128)))||
        (parameters.sender_userids!==undefined&&(!Array.isArray(parameters.sender_userids)||parameters.sender_userids.length<1||parameters.sender_userids.length>50||
          parameters.sender_userids.some(k=>typeof k!=='string'||!k.trim())))))throw new Error('Invalid scoped message search parameters');
    if(!/^[0-9a-f]{32}$/.test(traceId))throw new Error('Expected a 32 digit trace identifier');
    const payload={
      headers_json:JSON.stringify({'content-type':'application/json','x-openclaw-traceid':traceId}),
      arguments_json:JSON.stringify([baseUrl+spec.path,JSON.stringify(parameters)])
    };
    return await post({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'wecom_tool',arguments:{payload:JSON.stringify(payload)}}});
  }
};
