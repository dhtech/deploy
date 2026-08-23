# Production nftables ruleset: jumpgate SSH baseline plus per-role ports.
# Replaces the install-time baseline written by the deploy late script.
class dhfirewall (
  Array[Integer] $open_tcp  = [],
  Array[String]  $jumpgates = ['10.200.0.2'],
  Integer        $ssh_port  = 22,
) {
  file { '/etc/nftables.conf':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => epp('dhfirewall/nftables.conf.epp', {
      'open_tcp'  => $open_tcp,
      'jumpgates' => $jumpgates,
      'ssh_port'  => $ssh_port,
    }),
  }

  service { 'nftables':
    ensure    => running,
    enable    => true,
    subscribe => File['/etc/nftables.conf'],
  }
}
