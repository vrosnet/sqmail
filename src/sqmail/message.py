# Message abstraction.
# $Source: /cvsroot/sqmail/sqmail/src/sqmail/message.py,v $
# $State: Exp $

import os
import rfc822
import string
import sqmail.db
import cPickle
import cStringIO
import mimetools
import multifile
import types

class MIMEDecodeAbortException(Exception):
	pass

class Message:
	def __init__(self, id=0):
		self.id = id
		self.tofield = None
		self.fromfield = None
		self.realfromfield = None
		self.ccfield = None
		self.subjectfield = None
		self.date = None
		self.annotation = None
		self.readstatus = None
		self.headers = None
		self.body = None
		self.cursor = None
		self.readbuffer = None
		self.readingheaders = None
		self.fp = self
	
	def fetchheader(self, fieldname):
		if not self.id:
			raise RuntimeError, "Trying to fetch field `"+fieldname+"' from non-database message"
		if not self.cursor:
			self.cursor = sqmail.db.cursor()
		self.cursor.execute("SELECT %s FROM headers WHERE id = %d" % (fieldname, self.id))
		return self.cursor.fetchone()[0]
		
	def fetchheaders(self):
		if not self.id:
			raise RuntimeError, ("Trying to fetch headers from "
								 "non-database message")
		return sqmail.db.fetchone("SELECT header from bodies WHERE id = %s",
								  self.id)[0]

	def fetchbody(self):
		"""Return body from database as a string

		Retrieve in blocks of 960KB to avoid exceeding maximum packet
		size.
		"""
		blocksize, body, i = 983040, "", 1
		if not self.id:
			raise RuntimeError, ("Trying to fetch body from"
								 "non-database message")
		while 1:
			s = sqmail.db.fetchone("SELECT SUBSTRING(body, %d, %d) "
								   "from bodies WHERE id = %%s" %
								   (i, blocksize), self.id)[0]
			body = body + s
			i = i + blocksize
			if len(s) != blocksize: break
		return body
		
	def getid(self):
		if not self.id:
			raise RuntimeError, "Trying to fetch ID of non-database message"
		return self.id

	def getto(self):
		if self.tofield == None:
			fp = cStringIO.StringIO(self.fetchheader("tofield"))
			self.tofield = cPickle.load(fp)
			fp.close
		return self.tofield

	def getfrom(self):
		if self.fromfield == None:
			self.fromfield = self.fetchheader("fromfield")
			if (self.fromfield == None):
				self.fromfield = "(no address)"
		return self.fromfield
	
	def getrealfrom(self):
		if self.realfromfield == None:
			self.realfromfield = self.fetchheader("realfromfield")
		return self.realfromfield
	
	def getcc(self):
		if self.ccfield == None:
			self.ccfield = self.fetchheader("ccfield")
		return self.ccfield
	
	def getsubject(self):
		if self.subjectfield == None:
			self.subjectfield = self.fetchheader("subjectfield")
		return self.subjectfield
	
	def getdate(self):
		if self.date == None:
			self.date = self.fetchheader("unix_timestamp(date)")
			if self.date:
				self.date = float(self.date)
			else:
				self.date = 0.0
		return self.date
	
	def getannotation(self):
		if self.annotation == None:
			self.annotation = self.fetchheader("annotation")
		return self.annotation
	
	def setannotation(self, annotation):
		self.annotation = annotation

	def getreadstatus(self):
		if self.readstatus == None:
			self.readstatus = self.fetchheader("readstatus")
		return self.readstatus
	
	def getheaders(self):
		if self.headers == None:
			self.headers = self.fetchheaders()
		if self.headers == None:
			print "WARNING: msg id", self.id, "has no headers --- corrupt database?"
			self.headers = ""
		return self.headers
	
	def getbody(self):
		if self.body == None:
			b = self.fetchbody()
			# Experimental optimisation: we may not actually need
			# to cache the bodies (we don't look at them very
			# often, and it may help the memory leak).
			#self.body = b
		else:
			b = self.body
		if (b == None):
			print "WARNING: msg id", self.id, "has no body --- corrupt database?"
			b = ""
		return b
	
	def tosqlstring(self, o):
		fp = cStringIO.StringIO()
		cPickle.dump(o, fp)
		return sqmail.db.escape(fp.getvalue())
	
	def fromsqlstring(self, str):
		fp = cStringIO.StringIO(str)
		return cPickle.load(fp)

	def sanitiseaddresslist(self, msg, header):
		l = msg.getaddrlist(header)
		if (len(l) == 0):
			return ""
		r = ""
		for i in l:
			name = i[0]
			addr = i[1]
			if (name == ""):
				r = r + addr + " <" + addr + ">, "
			else:
				name = string.replace(name, "'", "")
				name = string.replace(name, '"', "")
				r = r + name + " <" + addr + ">, "
		return r[0:-2]

	def loadfrommessage(self, msg):
		self.tofield = msg.getaddrlist("To")
		f = msg.getaddr("From")
		self.fromfield = f[1]
		self.realfromfield = f[0]
		if not self.realfromfield:
			self.realfromfield = self.fromfield
		self.ccfield = msg.getaddrlist("Cc")
		if not self.ccfield:
			self.ccfield = ()
		self.subjectfield = msg.getheader("Subject")
		if not self.subjectfield:
			self.subjectfield = ""
		self.annotation = msg.getheader("X-SQmaiL-Annotation")
		if not self.annotation:
			self.annotation = ""
		self.readstatus = "Unread"
	
		# Work out the date the message arrived.

		r = ""
		for i in msg.getallmatchingheaders("Received"):
			r = r + i
		p = string.find(r, ";")
		if (p == -1):
			self.date = 0
		else:
			r = r[p+1:]
			r = rfc822.parsedate_tz(r)
			r = rfc822.mktime_tz(r)
			self.date = r

		self.headers = string.join(msg.headers, "")
		self.body = msg.fp.read()
		
	def loadfromstring(self, msgstring):
		fp = cStringIO.StringIO(msgstring)
		msg = rfc822.Message(fp)
		self.loadfrommessage(msg)

	def _writeheaderstodatabase(self):
		cmd = ""
		if self.tofield != None:
			cmd = cmd + "tofield = '"+self.tosqlstring(self.tofield)+"',"
		if (self.fromfield != None):
			cmd = cmd + "fromfield = '"+sqmail.db.escape(self.fromfield)+"',"
		if (self.realfromfield != None):
			cmd = cmd + "realfromfield = '"+sqmail.db.escape(self.realfromfield)+"',"
		if (self.ccfield != None):
			cmd = cmd + "ccfield = '"+self.tosqlstring(self.ccfield)+"',"
		if (self.subjectfield != None):
			cmd = cmd + "subjectfield = '"+sqmail.db.escape(self.subjectfield)+"',"
		if self.date:
			cmd = cmd + "date = from_unixtime("+str(float(self.date))+"),"
		if (self.annotation != None):
			cmd = cmd + "annotation = '"+sqmail.db.escape(self.annotation)+"',"
		if (self.readstatus != None):
			cmd = cmd + "readstatus = '"+self.readstatus+"',"
		if (cmd == ""):
			return
		cmd = "UPDATE headers SET %s WHERE id = %d" % (cmd[0:-1], self.id)
		self.cursor.execute(cmd)
	
	def _writebodiestodatabase(self):
		"""Write the message into the bodies table

		Because mysql's maximum packet length defaults to 1MB, write
		data in 960KB chunks if message is very long.

		"""
		blocksize = 983040
		if (self.headers != None):
			sqmail.db.execute("UPDATE bodies set header = %s WHERE id = %s",
							  (self.headers, self.id))
		if (self.body != None):
			sqmail.db.execute("UPDATE bodies set body = %s WHERE id = %s",
							  (self.body[:blocksize], self.id))
			for i in range(blocksize, len(self.body), blocksize):
				sqmail.db.execute("UPDATE bodies set body = "
								  "CONCAT(body, %s) WHERE id = %s",
								  (self.body[i:i+blocksize], self.id))

	def savealltodatabase(self):
		if not self.cursor:
			self.cursor = sqmail.db.cursor()
		sqmail.db.lock(self.cursor)
		if not self.id:
			# Need to create a new entry.
			self.id = sqmail.db.getnewid(self.cursor)
			self.cursor.execute("INSERT INTO headers (id) VALUES (%d)" %
								self.id)
			self.cursor.execute("INSERT INTO bodies (id) VALUES (%d)" %
								self.id)
		self._writeheaderstodatabase()
		self._writebodiestodatabase()
		sqmail.db.unlock(self.cursor)

	def markread(self):
		if not self.cursor:
			self.cursor = sqmail.db.cursor()
		if (self.getreadstatus() == "Unread"):
			self.cursor.execute("UPDATE headers SET readstatus = 'Read' WHERE id=%d" % self.id)
			self.readstatus = "Read"

	def mimedecode(self, msg=None, id=""):
		if not msg:
			self.rewind()
			msg = mimetools.Message(self, 0)
		type = msg.gettype()
		if (len(id) > 5):
			# Emergency abort!
			return [["(diagnostic)", "text/plain", \
				"Attachments too deeply nested --- aborting (probably hit the Multifile bug)", \
				id+"A"]]

		disposition = msg.getheader("Content-Disposition")
		disposition = sqmail.utils.parse_mimeheader(disposition)
		name = msg.getparam("name")
		index = 65
		if not name:
			name = sqmail.utils.get_mime_param(disposition, "filename")
		if not name:
			name = "<unnamed>"
		if (type[:10] == "multipart/"):
			multi = multifile.MultiFile(msg.fp, 0)
			multi.push(msg.getparam("boundary"))
			l = []
			while multi.next():
				l.append(self.mimedecode(mimetools.Message(multi, 0), id+chr(index))[0])
				index = index + 1
				if (index > 65+32):
					# Emergency abort!
					raise MIMEDecodeAbortException
			multi.pop()
			return [[name, type, l, ""]]
		else:
			encoding = msg.getencoding()
			if (encoding != "7bit") and (encoding != "8bit"):
				data = cStringIO.StringIO()
				mimetools.decode(msg.fp, data, msg.getencoding())
				return [[name, type, data.getvalue(), id]]
			else:
				return [[name, type, string.join(msg.fp.readlines(), ""), id]]

	def mimeflatten(self, data=None):
		if not data:
			data = self.mimedecode()
		output = []
		for i in range(len(data)):
			if (type(data[i][2]) == types.ListType):
				output.extend(self.mimeflatten(data[i][2]))
			else:
				output.append(data[i])
		return output
			
	def rfc822(self):
		self.rewind()
		return rfc822.Message(self, 0)

	def readline(self):
		if not self.readbuffer:
			self.readbuffer = cStringIO.StringIO(self.getheaders())
			self.fp = self
			self.readingheaders = 1
		if self.readingheaders:
			v = self.readbuffer.readline()
			if (v == ""):
				self.readbuffer = cStringIO.StringIO(self.getbody())
				v = "\n"
				self.readingheaders = 0
			return v
		return self.readbuffer.readline()

	def rewind(self):
		self.readbuffer = None

	def readlines(self):
		r = []
		while 1:
			s = self.readline()
			if (s == ""):
				break
			r.append(s)
		return r

# Revision History
# $Log: message.py,v $
# Revision 1.9  2001/06/15 02:23:10  bescoto
# Read and write messages in 960K chunks to avoid exceeding maximum mysql
# packet size.
#
# Revision 1.8  2001/04/25 12:51:46  dtrg
# Fixed generation of the In-Reply-To headers (it was looking for the
# Message-Id field of the original message in the wrong place and with the
# wrong spelling).
#
# Revision 1.7  2001/04/19 18:22:36  dtrg
# Added some bulletproofing against trying to render incorrect addresses.
#
# Revision 1.6  2001/03/09 10:34:14  dtrg
# When you do str(i) when i is a long, Python returns a string like "123L".
# This really upsets the SQL server. So I've rewritten large numbers of the
# SQL queries to use % syntax, which doesn't do that.
#
# Revision 1.5  2001/03/05 20:44:41  dtrg
# Lots of changes.
# * Added outgoing X-Face support (relies on netppm and compface).
# * Rearrange the FileSelector code now I understand about bound and unbound
# method calls.
# * Put in a workaround for the MimeReader bug, so that when given a message
# that triggers it, it fails cleanly and presents the user with the
# undecoded message rather than eating all the core and locking the system.
# * Put some sanity checking in VFolder so that attempts to access unknown
# vfolders are trapped cleanly, rather than triggering the
# create-new-vfolder code and falling over in a heap.
#
# Revision 1.4  2001/01/18 19:28:59  dtrg
# Added some bulletproofing for corrupt messages (sometimes generated when a
# send fails). Checks for a header or body block of None.
#
# Revision 1.3  2001/01/11 20:31:29  dtrg
# Fixed the last fix, which was broken and prevented any MIME attachments
# from being shown.
#
# Revision 1.2  2001/01/09 14:31:23  dtrg
# Added workaround for a Multifile bug that was causing lock-ups on bogus
# messages. Still doesn't work, but at least it doesn't work cleanly.
#
# Revision 1.1  2001/01/05 17:27:48  dtrg
# Initial version.
#

