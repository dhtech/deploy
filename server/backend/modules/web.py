# ENC generator for pkg "web": the port comes from the pkg parameter
# in ipplan, e.g. web(port=80).

def generate(host, params, manifest):
  return {'dhfirewall': {'open_tcp': [params.get('port', 80)]}}
