# Test-env site manifest: every node gets the production firewall role;
# per-role ports open on top of the jumpgate baseline.

node /^web1\./ {
  class { 'dhfirewall':
    open_tcp => [80, 443],
  }
}

node /^vault1\./ {
  class { 'dhfirewall':
    open_tcp => [8200],
  }
}

node /^puppet1\./ {
  class { 'dhfirewall':
    open_tcp => [8140],
  }
}

node default {
  include dhfirewall
}
