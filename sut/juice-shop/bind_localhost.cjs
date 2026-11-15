// Preloaded with `node -r` so Juice Shop listens on loopback only.
// Juice Shop calls `server.listen(port, cb)` without a host, which binds every
// interface. This patch injects the host from JUICE_SHOP_BIND_HOST (default
// 127.0.0.1) whenever listen() is called with a port and no host.
'use strict'
const net = require('node:net')

const host = process.env.JUICE_SHOP_BIND_HOST || '127.0.0.1'
const original = net.Server.prototype.listen

net.Server.prototype.listen = function patchedListen (...args) {
  const first = args[0]
  if (typeof first === 'number' || (typeof first === 'string' && /^\d+$/.test(first))) {
    if (typeof args[1] !== 'string') {
      args.splice(1, 0, host)
    }
  } else if (first && typeof first === 'object' && !Array.isArray(first) && first.port !== undefined && !first.host && !first.path) {
    args[0] = { ...first, host }
  }
  return original.apply(this, args)
}
