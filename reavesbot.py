#!/usr/bin/python
# https://github.com/justintv/Twitch-API/blob/master/IRC.md
# https://github.com/justintv/Twitch-API/tree/master/v3_resources
# reavesbot.py
# dave@evad.io

import re
import socket
from time import sleep
import string
import multiprocessing
from multiprocessing import reduction
from setproctitle import setproctitle
import redis
import requests
from config import *

################################################################################

def say(output_queue,msg):
	output_queue.put(u"PRIVMSG " + CHAN + " :" + msg)

################################################################################

def output_buffer_process(socket_pipe,output_queue):
	setproctitle("reavesbot: output_buffer_process")
	## incoming is a multiprocessing.Pipe
	# we block waiting for the socket to be given to us, timeout = None
	socket_pipe.poll(None)

	# now we grab the socket
	s = socket.fromfd(reduction.recv_handle(socket_pipe), socket.AF_INET, socket.SOCK_STREAM)

	print "output_buffer_process: ready"
	print "startup complete"

	# Now poll the incoming queue and send data as it comes in
	while True:
		data = output_queue.get()
		s.send(data.encode("utf-8") + u"\r\n")

		# we pause to prevent flooding the IRC server
		sleep(RATE)

################################################################################

def give_hearts_process(minutes,users):
	setproctitle("reavesbot: give_hearts_process")
	red = redis.StrictRedis()

	## every X minutes ask for who is online
	#sleep(minutes * 60)
	while True:
		## Make sure the stream is online before we give out hearts
		online = False

		try:
			r = requests.get('https://api.twitch.tv/kraken/streams/' + STREAM)
			if r.status_code == 200:
				js = r.json()
				if js['stream'] is not None:
					online = True
		except Exception as ex:
			print "WARNING: Could not check online status via API: " + str(type(ex)) + " " + str(ex) 
		
		if online:
			print "give_hearts_process: giving hearts"
			for user in users:
				try:
					## Get the existing score from redis
					score = red.get("hearts:" + user)
					if score is not None:
						score = int(score)
						score = score + 1
					else:
						score = 1

					red.set("hearts:" + user,score)

				except Exception as ex:
					print "EXCEPTION: " + str(type(ex)) + " " + str(ex)
		else:
			print "give_hearts_process: stream is offline"

		sleep(minutes * 60)

################################################################################

def command_uptime(output_queue):
	r = requests.get('https://decapi.me/twitch/uptime?channel=' + STREAM)
	if r.status_code == 200:
		say(output_queue,r.text)
	else:
		print "Error code returned from decapi.me: " + str(r.status_code)

################################################################################

readbuffer = ""
startupComplete = False

## Shared data
manager = multiprocessing.Manager()
users   = manager.list()

## title for master process
setproctitle("reavesbot: master")

## redis connection 
red = redis.StrictRedis()

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# twitch IRC servers send a PING 'About once every five minutes' or so
s.settimeout(SOCKET_TIMEOUT)
s.connect((HOST, PORT))
s.send("USER {}\r\n".format(NICK).encode("utf-8"))
s.send("PASS {}\r\n".format(PASS).encode("utf-8"))
s.send("NICK {}\r\n".format(NICK).encode("utf-8"))
s.send("JOIN {}\r\n".format(CHAN).encode("utf-8"))
s.send(u"CAP REQ :twitch.tv/membership\r\n")
s.send(u"CAP REQ :twitch.tv/commands\r\n")
s.send(u"CAP REQ :twitch.tv/tags\r\n")

while True:
	readbuffer = readbuffer + s.recv(1024)
	temp       = string.split(readbuffer, "\r\n")
	readbuffer = temp.pop()
		
	for response in temp:
		print "----------------------------------------------------------------"
		print "| RAW: '" + response + "'"

		### IRC messages have lots of optional bits.
		### they can start with @stuff to mean IRCv3 capabilities e.g. @tag or @tag;tag2;tag3. Its space seperated.
		## they can also start with :stuff where stuff is the prefix (who sent this)
		## or they start with just a straight command
		## we split the message up by spaces and work out stuff via horrible if statements

		tags     = None
		prefix   = None
		username = None
		command  = None
		args     = None

		parts  = response.split(" ")
		parts2 = response.split(" ")

		for part in parts:
			if part.startswith('@'):
				if tags is None:
					tags = part[1:]
					parts2.pop(0)

			elif part.startswith(':'):
				if prefix is None:
					prefix = part[1:]
					parts2.pop(0)
	
					if '!' in prefix:
						username = prefix.split("!",1)[0]

			else:
				command = part
				parts2.pop(0)
				args    = " ".join(parts2)
				break

		print "| tags:     '" + str(tags) + "'"
		print "| prefix:   '" + str(prefix) + "'"
		print "| username: '" + str(username) + "'"
		print "| command:  '" + str(command) + "'"
		print "| args:     '" + str(args) + "'"

		if command == 'PING':
			print "Server sent PING, sending PONG"
			s.send("PONG :tmi.twitch.tv\r\n".encode("utf-8"))

		# 353 = NAMES of who is online
		elif command == '353':
			if args is not None:
				## we only want the bits after the : character
				if ':' in args:
					names_str  = args.split(":",1)[1]
					names_list = names_str.split(" ")

					for name in names_list:
						if name not in users:
							users.append(name)

		elif command == 'JOIN':
			if username == "reavesbot" and args == CHAN:
				if not startupComplete:
					print "Joined " + CHAN
			
					parent, child = multiprocessing.Pipe()
					output_queue  = multiprocessing.Queue()
					output_buffer = multiprocessing.Process(target=output_buffer_process, args=(child,output_queue,))

					output_buffer.start()
					while not output_buffer.pid:
						time.sleep(.25)
		
					# send the socket
					reduction.send_handle(parent, s.fileno(), output_buffer.pid)

					# start the hearts giving process
					hearts_process = multiprocessing.Process(target=give_hearts_process, args=(HEARTS_WHEN,users))
					hearts_process.start()

					startupComplete = True
			else:
				if args is not None:
					if args == CHAN:
						if username not in users:
							users.append(username)

		elif command == "PART":
			if args is not None:
				if args == CHAN:
					if username in users:
						users.remove(username)

		elif command == "PRIVMSG":
			if args is not None:
				if ':' in args:
					message  = args.split(":",1)[1]
					print "[" + username + "] " + message.rstrip()

					if startupComplete:
						if message == "!uptime":
							multiprocessing.Process(target=command_uptime, args=(output_queue,)).start()

						elif message == "!hearts":
							score = red.get("hearts:" + username)
							if score is not None:
								say(output_queue,username + " has " + score + " <3")
							else:
								say(output_queue,username + " has no hearts yet! BibleThump")

						elif message.startswith("!setcmd"):
							if username in MASTERS:
								parts = message.split(" ")
								if len(parts) >= 3:
									cmd = parts[1]
									text = " ".join(parts[2:])

									if not cmd in ['uptime','hearts','setcmd', 'delcmd']:																		
										try:
											red.set("cmd:" + cmd,text)
											say(output_queue,"The command '" + cmd + "' has been set")
										except Exception as ex:
											say(output_queue,"An internal error occured")
											print "EXCEPTION: " + str(type(ex)) + " " + str(ex)

						elif message.startswith("!delcmd"):
							if username in MASTERS:
								parts = message.split(" ")
								if len(parts) == 2:
									cmd = parts[1]
					
									try:
										red.delete("cmd:" + cmd)
										say(output_queue,"The command '" + cmd + "' has been deleted")
									except Exception as ex:
										say(output_queue,"An internal error occured")
										print "EXCEPTION: " + str(type(ex)) + " " + str(ex)

						elif message.startswith('!'):
							if len(message.split(" ")) == 1:
								cmd = message[1:]
								try:
									text = red.get("cmd:" + cmd)
								except Exception as ex:
									print "EXCEPTION: " + str(type(ex)) + " " + str(ex)							

								if text is not None:
									say(output_queue,text)

