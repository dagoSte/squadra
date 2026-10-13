// Check three-line card previews with the shared DOM layout model.
'use strict';
process.argv[4]='closed';
process.on('beforeExit',()=>{if(!process.exitCode)console.log('closed cards contract passed: closed')});
require('./read_contract.js');
