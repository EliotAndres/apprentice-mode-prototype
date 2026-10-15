// Only sites that bot-wall the sandbox's datacenter IP go through the residential proxy (metered).
function FindProxyForURL(url, host) {
  var proxied = ["xe.com", "pypi.org", "stackoverflow.com", "stackexchange.com", "sstatic.net", "npmjs.com", "npmjs.org", "allrecipes.com", "getyourguide.com", "viator.com"];
  // OpenStreetMap rate-limits the sandbox host (429); proxy the site and search, not the heavy map tiles.
  if (host == "www.openstreetmap.org" || host == "nominatim.openstreetmap.org") return "PROXY 127.0.0.1:8899";
  for (var i = 0; i < proxied.length; i++)
    if (host == proxied[i] || dnsDomainIs(host, "." + proxied[i])) return "PROXY 127.0.0.1:8899";
  return "DIRECT";
}
