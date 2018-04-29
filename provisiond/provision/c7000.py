# C7000 SOAP stuff

import collections
import libxml2
import requests
import sys


Session = collections.namedtuple('Session', ('user', 'key', 'host'))

MOMENTARY_PRESS = 'MOMENTARY_PRESS'
COLD_BOOT = 'COLD_BOOT'


class Error(Exception):
  """Base error class for this module."""


def _post(session, data):
  cookies = {
      'encLocalKey': session.key,
      'encLocalUser': session.user
  }
  response = requests.post(
      'https://%s/hpoa' % session.host, data, verify=False, cookies=cookies)
  doc = libxml2.parseDoc(response.text)
  ctxt = doc.xpathNewContext()
  if ctxt.xpathEval('//*[local-name()="returnCodeOk"]'):
    return True
  raise Error(response.text)


def login(host, username, password):
  data = """
<?xml version="1.0"?>
  <SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:hpoa="hpoa.xsd">
    <SOAP-ENV:Body>
      <hpoa:userLogIn>
        <hpoa:username>{username}</hpoa:username>
        <hpoa:password>{password}</hpoa:password>
      </hpoa:userLogIn>
    </SOAP-ENV:Body>
  </SOAP-ENV:Envelope>""".format(username=username, password=password)
  response = requests.post('https://%s/hpoa' % host, data, verify=False)
  doc = libxml2.parseDoc(response.text)
  ctxt = doc.xpathNewContext()
  session_node, = ctxt.xpathEval('//*[local-name()="oaSessionKey"]')
  return Session(username, session_node.get_content(), host)


def netboot(session, bay):
  data = """
<?xml version="1.0"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:hpoa="hpoa.xsd">
  <SOAP-ENV:Header>
    <wsse:Security SOAP-ENV:mustUnderstand="true">
      <hpoa:HpOaSessionKeyToken>
        <hpoa:oaSessionKey>{key}</hpoa:oaSessionKey>
      </hpoa:HpOaSessionKeyToken>
    </wsse:Security>
  </SOAP-ENV:Header>
  <SOAP-ENV:Body>
    <hpoa:setBladeOneTimeBootEx>
      <hpoa:bayNumber>{bay}</hpoa:bayNumber>
      <hpoa:oneTimeBootDevice>ONE_TIME_BOOT_NO_CHANGE</hpoa:oneTimeBootDevice>
      <hpoa:oneTimeBootAgent>PXE</hpoa:oneTimeBootAgent>
      <hpoa:oneTimeBypassF1F2Messages>true</hpoa:oneTimeBypassF1F2Messages>
      <hpoa:toggleBootMode>false</hpoa:toggleBootMode>
    </hpoa:setBladeOneTimeBootEx>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>""".format(key=session.key, bay=bay)
  _post(session, data)


def power_on(session, bay, power_type=MOMENTARY_PRESS):
  data = """
<?xml version="1.0"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:hpoa="hpoa.xsd">
  <SOAP-ENV:Header>
    <wsse:Security SOAP-ENV:mustUnderstand="true">
      <hpoa:HpOaSessionKeyToken>
        <hpoa:oaSessionKey>{key}</hpoa:oaSessionKey>
      </hpoa:HpOaSessionKeyToken>
    </wsse:Security>
  </SOAP-ENV:Header>
  <SOAP-ENV:Body>
    <hpoa:setBladePower>
      <hpoa:bayNumber>{bay}</hpoa:bayNumber>
      <hpoa:power>{power_type}</hpoa:power>
    </hpoa:setBladePower>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>""".format(key=session.key, bay=bay, power_type=power_type)
  _post(session, data)


def setup_boot_order(session, bay):
  data = """
<?xml version="1.0"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:hpoa="hpoa.xsd">
  <SOAP-ENV:Header>
    <wsse:Security SOAP-ENV:mustUnderstand="true">
      <hpoa:HpOaSessionKeyToken>
        <hpoa:oaSessionKey>{key}</hpoa:oaSessionKey>
      </hpoa:HpOaSessionKeyToken>
    </wsse:Security>
  </SOAP-ENV:Header>
  <SOAP-ENV:Body>
    <hpoa:setBladeIplBootPriorityEx>
      <hpoa:bayNumber>{bay}</hpoa:bayNumber>
      <hpoa:bladeIplBootArrayEx xmlns:hpoa="hpoa.xsd">
        <hpoa:ipl xmlns:hpoa="hpoa.xsd">
          <hpoa:bootPriority>1</hpoa:bootPriority>
          <hpoa:bootDevIdentifier>3</hpoa:bootDevIdentifier>
        </hpoa:ipl>
        <hpoa:ipl xmlns:hpoa="hpoa.xsd">
          <hpoa:bootPriority>2</hpoa:bootPriority>
          <hpoa:bootDevIdentifier>5</hpoa:bootDevIdentifier>
        </hpoa:ipl>
        <hpoa:ipl xmlns:hpoa="hpoa.xsd">
          <hpoa:bootPriority>3</hpoa:bootPriority>
          <hpoa:bootDevIdentifier>1</hpoa:bootDevIdentifier>
        </hpoa:ipl>
        <hpoa:ipl xmlns:hpoa="hpoa.xsd">
          <hpoa:bootPriority>4</hpoa:bootPriority>
          <hpoa:bootDevIdentifier>4</hpoa:bootDevIdentifier>
        </hpoa:ipl>
        <hpoa:ipl xmlns:hpoa="hpoa.xsd">
          <hpoa:bootPriority>5</hpoa:bootPriority>
          <hpoa:bootDevIdentifier>2</hpoa:bootDevIdentifier>
        </hpoa:ipl>
      </hpoa:bladeIplBootArrayEx>
    </hpoa:setBladeIplBootPriorityEx>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>""".format(key=session.key, bay=bay)
  _post(session, data)


if __name__ == '__main__':
  session = login('172.16.0.30', sys.argv[1], sys.argv[2])

  setup_boot_order(session, 3)
  netboot(session, 3)
  power_on(session, 3, MOMENTARY_PRESS)


  print 'hello'
