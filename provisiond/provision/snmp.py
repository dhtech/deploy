import collections
import netsnmp
import time

ResultTuple = collections.namedtuple('ResultTuple', ['value', 'type'])


class Error(Exception):
  """Base error class for this module."""


class TimeoutError(Error):
  """Timeout talking to the device."""


class SnmpError(Error):
  """A SNMP error occurred."""


def session_v2(host, community):
  return netsnmp.Session(Version=2, DestHost=host, Community=community,
      UseNumeric=1, Timeout=10000000, Retries=3)


def walk(sess, oid):
  ret = {}
  nextoid = oid
  offset = 0

  # Abort the walk when it exits the OID tree we are interested in
  while nextoid.startswith(oid):
    var_list = netsnmp.VarList(netsnmp.Varbind(nextoid, offset))
    sess.getbulk(nonrepeaters=0, maxrepetitions=256, varlist=var_list)
    #Sleep because the snmp walk times out //ventris
    time.sleep ( 1 )

    if sess.ErrorStr == 'Timeout':
      raise TimeoutError(
          'Timeout getting %s' % nextoid)
    if sess.ErrorStr != '':
      raise SnmpError('SNMP error while walking host: %s' % sess.ErrorStr)

    for result in var_list:
      currentoid = '%s.%s' % (result.tag, int(result.iid))
      # We don't want to save extra oids that the bulk walk might have
      # contained.
      if not currentoid.startswith(oid):
        break
      ret[currentoid] = ResultTuple(result.val, result.type)
    # Continue bulk walk
    offset = int(var_list[-1].iid)
    nextoid = var_list[-1].tag
  return ret


def get(sess, oid):
  var_list = netsnmp.VarList(netsnmp.Varbind(oid))
  return sess.get(var_list)[0]
