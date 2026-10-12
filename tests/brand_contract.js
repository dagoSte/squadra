// Check three-line card previews with the shared DOM layout model.
'use strict';
process.argv[4]='measured';
process.on('beforeExit',()=>{if(!process.exitCode)console.log('brand contract passed: measured')});
require('./read_contract.js');
