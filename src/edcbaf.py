#!/usr/local/bin/python
# -*- coding: utf-8 -*-
'''edcbaf
edcbaf.sl3

remove message if headers['From'] contains '<info@twitter.com>'

default mailboxes ?
 inbox <- ?
 draft <- ?
 sent <- ?
 junk <- spam
 trash <- ?
'''

import sys, os
import getpass
import traceback
import re
import sre_constants
import time
import datetime
import logging
import imaplib
import email.Parser
import email.header
import yaml

APP_NAME = os.path.basename(sys.argv[0]).split('.')[0]
BASE_DIR = os.path.dirname(__file__)
LOGFILE = '%s/%s.log' % (BASE_DIR, APP_NAME)

DBF = '/private/edcbaf.yaml'
SVR = raw_input('imaps-server: ')
PRT = 993
ACT = map(lambda r: r['act'], yaml.load(open(DBF, 'rb').read())['yp'])
PID = getpass.getpass()

class ClsTool(object):
  def __init__(self, name, basedir, eid):
    self.name, self.basedir, self.eid = name, basedir, eid
    self.logger = logging.getLogger('%s: %s' % (self.name, self.eid))

  def guess_dec(self, u, c):
    if not isinstance(u, unicode):
      self.logger.debug('[]%s' % ''.join(('%02x' % ord(b)) for b in u[:4]))
      code = ['utf-8', 'euc-jp', 'cp932', 'iso-2022-jp', 'latin-1', 'ascii']
      for cd in ([c] if c else []) + code:
        try:
          u = u.decode(cd)
          break
        except (UnicodeDecodeError, LookupError):
          continue
      else:
        u = u.decode('latin-1', 'replace')
    return u

  def dec_mime_header(self, s):
    lst = []
    if s is None:
      return u''
    for l in s.split('\n'):
      try:
        for d in email.header.decode_header(l):
          lst.append(self.guess_dec(d[0], d[1]))
      except email.errors.HeaderParseError:
        continue
    return u''.join(lst) #.encode('utf-8')

  def entity_check(self, entity, depth):
    lst = []
    cs = entity.get_charsets()
    self.logger.debug('[]%*s(depth: %d, %s, %d)' % (
      depth + 1, ' ', depth, entity.get_content_type(), len(cs) - 1))
    if entity.is_multipart():
      for i, c in enumerate(cs[1:]):
        self.logger.debug('[]%*s(depth: %d, e: %d, charset: %s)' % (
          depth + 1, ' ', depth, i, c))
        try:
          lst += self.entity_check(entity.get_payload(i), depth + 1)
        except IndexError:
          pass
    else:
      if entity.get_content_maintype() == 'text':
        c = entity.get_content_charset() # cs[0]
        self.logger.debug('[]%*s(depth: %d, charset: %s)' % (
          depth + 1, ' ', depth, c))
        u = self.guess_dec(entity.get_payload(None, True), c)
        self.logger.debug('\n'.join(
          ('[]%s' % l) for l in u.encode('utf-8', 'replace').split('\n')))
        e = email.Parser.Parser().parsestr(u.encode('iso-2022-jp', 'replace'))
        if e.is_multipart():
          lst += self.entity_check(e, depth + 1)
        else:
          lst.append(u)
    self.logger.debug('[]%*s--------(depth %d)' % (depth + 1, ' ', depth))
    # *** !!! CAUTION !!! ***
    # (call entity_received0_check only when depth > 0) (text ? head ?)
    # create file for each (multi) part ?
    return lst # unicode

  def getdt_and_save(self):
    dn = '%s/act/%s' % (self.basedir, self.eid) # make branch for each eid
    if self.mid is None:
      self.dt = datetime.datetime.now()
      pt = dn
      if not os.path.exists(pt): os.mkdir(pt)
      for n, d in ((4, self.dt.year), (2, self.dt.month), (2, self.dt.day)):
        pt = '%s/%0*d' % (pt, n, d)
        if not os.path.exists(pt): os.mkdir(pt)
      self.fname = '%s/%s.%06d.%08d.%s.log' % (
        pt, datetime.datetime.strftime(self.dt, '%Y%m%d.%H%M%S'),
        self.dt.microsecond, os.getpid(), self.eid)
    else:
      q = re.compile(r'[<@>]', re.I)
      rl = reversed(self.mid.split('@', 1))
      safemid = '_._'.join(re.sub(q, '_', _) for _ in rl)
      self.fname = '%s/%s.msg' % (dn, safemid)
    self.logger.debug(self.fname)
    ofp = open(self.fname, 'wb') # overwrite (assume same name)
    ofp.write(self.s)
    ofp.close()

  def readact(self, dat):
    self.s = dat
    self.msg = email.Parser.Parser().parsestr(self.s)
    self.mid = self.msg['Message-Id']
    self.getdt_and_save()
    self.snd = self.msg['From']
    self.rcp = self.msg['To']
    self.sbj = self.msg['Subject']
    self.rcv = self.msg.get_all('Received') # local may be None !!! CAUTION !!!
    if self.rcv is None: self.rcv = []
    self.logger.debug('--\n%s\n--\n' % '\n'.join(self.rcv))
    self.logger.info(
      'eid=[%s], size=%d, id=[%s], from=[%s], to=[%s]' % (
        self.eid, len(self.s), self.mid, self.snd, self.rcp))
    sndr = self.dec_mime_header(self.snd)
    rcpt = self.dec_mime_header(self.rcp)
    subj = self.dec_mime_header(self.sbj)
    whole_body = u'\n'.join(self.entity_check(self.msg, 0))
    return (sndr, rcpt, subj, whole_body)

class ClsFetch(object):
  def __init__(self, **cf):
    self.name, self.basedir, self.act = cf['name'], cf['basedir'], cf['act']
    self.logger = logging.getLogger('%s: %s' % (self.name, self.act))
    self.logger.info('\n%s' % ('+' * 72))
    self.logger.info('\n%s' % self.act)
    self.dn = '%s/act/%s' % (self.basedir, self.act)
    if not os.path.exists(self.dn): os.mkdir(self.dn)
    self.ct = ClsTool(self.name, self.basedir, self.act)

  def readmsg(self, mode, nu, rd):
    '''mode: 0=number/1=uid, nu: number/uid integer'''
    r, d = rd
    # mode==0
    #  ('OK', [('num (RFC822 {...}', '<<MSG>>'), ')',
    #          'num (FLAGS (\\Seen *))'])
    # mode==1
    #  ('OK', [('num (UID uid RFC822 {...}', '<<MSG>>'), ')',
    #          'num (FLAGS (\\Seen *))'])
    self.logger.info(
      '%s[%s]<<...>>%s[%s]' % (r, d[0][0], d[1], d[2])) # OK[...]<<M>>)[...]
    s = d[0][1].replace('\x0D', '')
    # self.logger.debug('%s[%s][%s]' % (r, nu, s)) # OK[nu][msg(hdr+bdy)]
    ul = self.ct.readact(s)
    self.logger.debug(ul[2]) # subj u'' -> 'utf-8'
    sys.stderr.write('%s\n' % ul[2].encode('cp932', 'replace')) # subj encode
    self.logger.debug(ul[3]) # whole_body u'' -> 'utf-8'

  def dummy(self):
    for i in range(1, 7):
      fn = '%s/_msg_%08d_.msg' % (self.dn, i)
      if not os.path.exists(fn): continue
      f = open(fn, 'rb')
      m = f.read()
      f.close()
      r = 'OK'
      d = [('%d (RFC822 {%d}' % (i, len(m)), m), ')', '%d (FLAGS (\\Seen)' % i]
      self.readmsg(0, i, (r, d))

  def connact(self):
    m = imaplib.IMAP4_SSL(SVR, PRT)
    m.login(self.act, PID)
    # ('OK', ['AUTHENTICATE completed - Mailbox size in bytes is 4346874'])
    m.select()
    # ('OK', ['56'])

    '''# dangerous way (fetch and delete by num)
    res, dat = m.search(None, 'ALL')
    # ('OK', ['1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 '\
    #         '21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 '\
    #         '41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 '])
    for num in dat[0].split(): # num: string
      self.readmsg(0, int(num), m.fetch(num, '(RFC822)'))
      m.store(num, '+FLAGS', '\\Deleted') # *** !!! CAUTION !!! ***
    m.expunge() # *** !!! CAUTION !!! *** # may be deleted without call this
    '''

    '''# safety (fetch and delete by uid)
    res, dat = m.uid('SEARCH', 'ALL')
    # ('OK', ['205 207 '])
    m.uid('FETCH', '205:207', '(FLAGS UID)')
    # ('OK', ['1 (FLAGS (\\Recent) UID 205)', '2 (FLAGS (\\Recent) UID 207)'])
    m.uid('FETCH', '205:207', '(RFC822)') # test multi uid fetch
    # ('OK', [('1 (UID 205 RFC822 {4372}', '<<MSG>>'), ')',
    #         ('2 (UID 207 RFC822 {4204}', '<<MSG>>'), ')',
    #         '1 (FLAGS (\\Seen \\Recent))', '2 (FLAGS (\\Seen \\Recent))'])
    m.uid('STORE', '205', '+FLAGS', '(\\Deleted)')
    # ('OK', ['1 (FLAGS (\\Deleted \\Seen) UID 205)'])
    '''

    res, dat = m.uid('SEARCH', 'ALL')
    for uid in dat[0].split(): # uid: string
      self.readmsg(1, int(uid), m.uid('FETCH', uid, '(RFC822)'))
      m.uid('STORE', uid, '+FLAGS', '(\\Deleted)')

    m.close()
    # ('OK', ['CLOSE completed - Now in authenticated state'])
    m.logout()
    # ('BYE', ['IMAP4rev1 Server logging out'])

if __name__ == '__main__':
  # sys.path.append(os.path.dirname(__file__))
  # sys.path.append(os.path.join(os.path.dirname(__file__), 'application'))
  fmt = '%(asctime)s [%(name)-8s:%(process)8s] %(levelname)-8s: %(message)s'
  logging.basicConfig(level=logging.DEBUG,
    format=fmt, datefmt='%Y-%m-%d %H:%M:%S',
    filename=LOGFILE, filemode='a')
  ''' '''
  console = logging.StreamHandler()
  console.setLevel(logging.INFO) #.DEBUG)
  console.setFormatter(logging.Formatter(fmt))
  logging.getLogger('').addHandler(console)
  ''' '''

  logging.info('%s start %s' % (APP_NAME, BASE_DIR))
  for act in ACT:
    cf = ClsFetch(name=APP_NAME, basedir=BASE_DIR, act=act)
    if True:
      cf.connact()
    else:
      cf.dummy()
  logging.info('%s stopped' % (APP_NAME, ))
