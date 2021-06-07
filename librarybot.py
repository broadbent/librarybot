#!/usr/bin/env python
"""A Discord bot for managing use of a physical book library."""

import os
import random
import sqlite3
import datetime
import asyncio
import csv
import urllib.request
import yaml
import sys

import isbnlib
import discord
from discord.ext import commands
from dotenv import load_dotenv

__author__ = "Matthew Broadbent"
__copyright__ = "Copyright 2021, Matthew Broadbent"
__credits__ = ["Matthew Broadbent"]
__license__ = "GPL"
__version__ = "0.1.0"
__maintainer__ = "Matthew Broadbent"
__email__ = "matt@matthewbroadbent.net"
__status__ = "Development"

if __name__ == '__main__':

	db = sqlite3.connect("library.db")
	cursor = db.cursor()

	load_dotenv()
	TOKEN = os.getenv('DISCORD_TOKEN')

	config_path = "config.yml"
	try:
		config_path = sys.argv[1]
	except IndexError:
		pass

	with open(config_path, "r") as ymlfile:
		cfg = yaml.safe_load(ymlfile)
	DISCORD_CHANNEL = cfg['discord']['channel']
	ADMIN_USER = cfg['discord']['admin_user']
	ABOUT = cfg['library']['about']
	BORROW_MESSAGE = cfg['library']['borrow_message']
	LOAN_PERIOD = int(cfg['library']['loans']['period'])
	MAX_LOANS = int(cfg['library']['loans']['max'])

	HAPPY_EMOJI = [':grin:', ':smile:', ':smiley:', ':slight_smile:', ':grinning:']
	SAD_EMOJI = [':sob:', ':cry:', ':worried:', ':pleading_face:', ':slight_frown:']

	help_command = commands.DefaultHelpCommand(no_category='Commands')

	bot = commands.Bot(
		command_prefix=commands.when_mentioned_or('!'),
		description='A Discord bot for managing and maintaining a physical book library.',
		help_command=help_command
	)


@bot.command(name='init', pass_context=True, hidden=True)
async def init(ctx, path: str = 'books.csv'):
	"""Initialise bot.

	Setup of database schema.

	Note:
		Only required once.

	Args:
		ctx: Discord message content
		path: Path of file to load books from.

	"""
	db.execute("PRAGMA foreign_keys = 1")
	cursor.execute(
		"CREATE TABLE IF NOT EXISTS books (title TEXT NOT NULL, binding TEXT NOT NULL, authors TEXT NOT NULL, series TEXT, "
		"available INTEGER NOT NULL, isbn TEXT PRIMARY KEY, location TEXT)")
	cursor.execute(
		"CREATE TABLE IF NOT EXISTS users (username TEXT NOT NULL, userid INTEGER PRIMARY KEY NOT NULL, banned BOOLEAN)")
	cursor.execute(
		"CREATE TABLE IF NOT EXISTS loans (rdate TIME, bdate TIME NOT NULL, estrdate TIME NOT NULL, returned BOOLEAN, "
		"userid INTEGER, isbn TEXT, FOREIGN KEY (userid) REFERENCES users (userid), FOREIGN KEY (isbn) REFERENCES books ("
		"isbn))")
	await ctx.invoke(bot.get_command('load'), path=path)
	await respond(ctx, ['Bot successfully initialised.'], admin=True)


@bot.command(name='load', pass_context=True, hidden=True)
async def load(ctx, path: str = 'books.csv'):
	"""Load books from a CSV file on disk.

	Args:
		ctx: Discord message content
		path: Path of file to load books from.
	"""
	if await auth_check(ctx, ):
		with open(path) as csv_file:
			csv_reader = csv.reader(csv_file, delimiter=',')
			line_count = 0
			for row in csv_reader:
				if line_count == 0:
					line_count += 1
				else:
					row = [None if x == '' else x for x in row]  # Convert empty values into None types
					isbn = row[5]
					if isbnlib.is_isbn13(isbn):
						try:
							cursor.execute("""
													INSERT INTO books ('title', 'binding', 'authors', 'series', 'available', 'isbn', 'location')
													VALUES (?, ?, ? ,? ,? ,?, ?)""", row[:-1])
							line_count += 1
						except sqlite3.IntegrityError:
							await respond(ctx, ['Duplicate book found.'], admin=True)
					else:
						await respond(ctx, ['Invalid ISBN.'], admin=True)
		db.commit()
		await respond(ctx, ['Load complete.', 'Loaded ' + str(line_count - 1) + ' books.'], admin=True)


@bot.command(pass_context=True, name='search')
async def search(ctx, scope: str = 'all', attr: str = '*', value: str = '*'):
	"""Search the library for books.

	Examples:
		Search the whole library:
			!search

		Search for available books across the whole library:
			!search available

		Search for all books from Dan Abnett:
			!search all authors abnett

		Search for available books from the Horus Heresy series:
			!search available series heresy

	Args:
		scope (str): scope to perform search in. 'all' to search amongst all books, 'available' for those available for
			booking, 'loaned' for those already loaned out.
		attr (str): field to search in. Fields include: title, authors, series and isbn.
		value (str): term to search for e.g. abnett

	"""
	res = []
	if scope == 'all':
		if attr == '*':
			res = cursor.execute("SELECT * FROM books ORDER BY title").fetchall()
		else:
			res = cursor.execute(
				"SELECT * FROM books WHERE " + attr + " LIKE '%" + value + "%' ORDER BY title;").fetchall()
	elif scope == 'available':
		res = cursor.execute(
			"SELECT * FROM books WHERE " + attr + " LIKE '%" + value + "%' AND available >= 1 ORDER BY title;").fetchall()
	elif scope == 'unavailable':
		res = cursor.execute(
			"SELECT * FROM books WHERE " + attr + " LIKE '%" + value + "%' AND available = 0 ORDER BY title;").fetchall()
	res = format_book_records(res)
	await respond(ctx, res, dm=True)


@bot.command(name='loans', pass_context=True, hidden=True)
async def loans(ctx, scope: str = 'all'):
	"""List loans within a given scope.

	Args:
		ctx: Discord message context.
		scope: basic filter for lookup. Default is 'all' loans. Includes, already 'returned', currently 'out' and
			those 'overdue'.

	"""
	if await auth_check(ctx, ):
		res = []
		if scope == 'all':
			res = cursor.execute("SELECT * FROM loans").fetchall()
		if scope == 'returned':
			res = cursor.execute("SELECT * FROM loans WHERE returned IS TRUE").fetchall()
		if scope == 'out':
			res = cursor.execute("SELECT * FROM loans WHERE returned IS FALSE").fetchall()
		if scope == 'overdue':
			res = cursor.execute("SELECT * FROM loans WHERE estrdate < '" + str(
				datetime.datetime.now()) + "' AND returned IS FALSE;").fetchall()
		await respond(ctx, res, dm=True, fast=True)


@bot.command(name='users', pass_context=True, hidden=True)
async def users_(ctx):
	"""Fetch a list of users."""
	users = cursor.execute("SELECT * FROM users").fetchall()
	await respond(ctx, users, admin=True, fast=True)


@bot.command(name='version', pass_context=True, hidden=True)
async def version(ctx, ):
	"""Print the version of this bot."""
	if await auth_check(ctx):
		await respond(ctx, [__version__], admin=True)


@bot.command(name='about', pass_context=True, help='Learn more about this library')
async def about(ctx, ):
	"""Learn more about this library."""
	await respond(ctx, [ABOUT])


@bot.command(name='issue', pass_context=True, help='Report an issue to the administrator')
async def issue_(ctx, issue: str):
	"""Report an issue to the administrator. Can be a problem with the system/bot or the library.

	Args:
		issue: the issue to report to the administrator

	"""
	user_id = str(ctx.message.author.id)
	user = await bot.fetch_user(int(user_id))
	await respond(ctx, ["Thanks! You're issue has been forwarded to the administrator " + random.choice(HAPPY_EMOJI)])
	await respond(ctx, ['Issue reported by: ' + str(user), issue], admin=True)


@bot.command(name='borrow', pass_context=True, help='Borrow a book from the library')
async def borrow(ctx, isbn: str):
	"""Borrow a book from the library.

	Examples:
		Borrow 'Lone Wolves' from the library:
			!borrow 9781789993158

	Args:
		isbn: ISBN number of the book to borrow

	"""
	user_id = ctx.message.author.id
	user = str(await bot.fetch_user(user_id))
	res = cursor.execute("SELECT * FROM users WHERE userid = '" + str(user_id) + "';").fetchone()
	messages = []
	if res is None:
		messages.append('Welcome to the library <@' + str(user_id) + '>! ' + random.choice(HAPPY_EMOJI))
		cursor.execute("INSERT INTO users VALUES ('" + str(user) + "'," + str(user_id) + ", 0)")
		db.commit()
	elif res[2]:
		await respond(ctx, ["It looks like you're banned from borrowing books! " + random.choice(SAD_EMOJI),
							"Please message <@" + str(ADMIN_USER) + "> if you think is a mistake.\n"])
		return
	unique_loan = len(cursor.execute(
		"SELECT userid FROM loans WHERE userid = '" + str(user_id) + "' AND isbn = '" + str(
			isbn) + "' AND returned IS FALSE;").fetchall())
	if unique_loan == 0:
		current_loans = len(cursor.execute(
			"SELECT isbn FROM loans WHERE userid = '" + str(user_id) + "' AND returned IS FALSE;").fetchall())
		if current_loans < MAX_LOANS:
			available = cursor.execute(
				"SELECT * FROM books WHERE isbn IS '" + isbn + "' AND available >= 1;").fetchone()
			if available is not None:
				cursor.execute(
					"UPDATE books SET available = available - 1 WHERE isbn IS '" + isbn + "' AND available >= 1;")
				cursor.execute("INSERT INTO loans VALUES (NULL, '" + str(datetime.datetime.now()) + "', '" + str(
					datetime.datetime.now() + datetime.timedelta(days=LOAN_PERIOD)) + "', FALSE, " + str(
					user_id) + ", '" + isbn + "');")
				db.commit()
				messages.append('Great! The book is yours. ' + random.choice(HAPPY_EMOJI))
				messages.append(BORROW_MESSAGE)
				await respond(ctx, ["New Loan from " + user, isbn], admin=True)
			else:
				messages.append("I'm sorry, that book isn't available. " + random.choice(SAD_EMOJI))
		else:
			messages.append("I'm sorry, it looks like you've reached your loan limit. " + random.choice(SAD_EMOJI))
	else:
		messages.append(
			"Sorry, you can't borrow that; it looks like you already have a copy on loan!  " + random.choice(SAD_EMOJI))
	await respond(ctx, messages, dm=True)


@bot.command(name='desc', pass_context=True, help='Show a short description for a book')
async def desc(ctx, isbn: str):
	"""Show a short description for a book.

	Args:
		isbn: ISBN number of the book you'd like the description for.

	"""
	description = isbnlib.desc(isbn)
	if description:
		await respond(ctx, ["Here's a brief description of the book:", description])
	else:
		await respond(ctx, [
			"Sorry, it doesn't look like we could find a description of that book " + random.choice(SAD_EMOJI)])


@bot.command(name='cover', pass_context=True, help='Fetch an image of the cover for a book')
async def cover(ctx, isbn: str):
	"""Fetch an image of the cover for a book.

	Args:
		isbn: ISBN number of the you'd like the cover of.

	"""
	try:
		cover_url = isbnlib.cover(isbn)['smallThumbnail']
		url_hash = str(hash(cover_url)) + '.jpg'
		urllib.request.urlretrieve(cover_url, url_hash)
		with open(url_hash, 'rb') as f:
			picture = discord.File(f)
			await ctx.send(file=picture)
		os.remove(url_hash)
	except KeyError:
		await respond(ctx, [
			"Sorry, we couldn't find an image of that book's cover " + random.choice(SAD_EMOJI)])


@bot.command(name='suprise', pass_context=True, help='Display a random book from the library')
async def suprise(ctx):
	"""Display a random book from the library."""
	book = cursor.execute("SELECT * FROM books ORDER BY RANDOM() LIMIT 1;").fetchone()
	await respond(ctx, format_book_records([book]))
	await ctx.invoke(bot.get_command('desc'), isbn=book[5])
	await ctx.invoke(bot.get_command('cover'), isbn=book[5])


@bot.command(name='due', pass_context=True, help='Check which books you have loaned and when they are due')
async def due(ctx):
	"""Check which books you have loaned and when they are due.

		Examples:
			Check your current loans:
				!due

	"""
	user_id = ctx.message.author.id
	res = cursor.execute(
		"SELECT isbn, estrdate FROM loans WHERE userid = '" + str(user_id) + "' AND returned IS FALSE;").fetchall()
	if len(res) == 0:
		await respond(ctx, [
			"It looks like you don't have any books loaned to you at the moment " + random.choice(SAD_EMOJI) + "\n",
			"Type `!help search` to find out how to search for books, and `!help borrow` for details " + "of how to get one! "
			+ random.choice(HAPPY_EMOJI) + "\n"])
	else:
		books = []
		for (isbn, estrdate) in res:
			book = cursor.execute("SELECT * FROM books WHERE isbn = '" + isbn + "';").fetchone()
			date_obj = datetime.datetime.strptime(estrdate, '%Y-%m-%d %H:%M:%S.%f')
			return_date = date_obj.date()
			remaining = (date_obj - datetime.datetime.now()).days + 1
			books.append((book, return_date, remaining))
		messages = (format_book_records(books, display_due_details=True))
		await respond(ctx, messages, dm=True)


@bot.command(name='renew', pass_context=True, hidden=True)
async def renew(ctx, isbn: str, userid: str, days: int):
	"""Renew a book and extend the estimated return date.

	Args:
		ctx: Discord message context.
		isbn: the ISBN of the book to renew.
		userid: username of the user to renew the book for.
		days: the number of days to renew the book for.

	"""
	if await auth_check(ctx):
		new_date = datetime.datetime.now() + datetime.timedelta(days=days)
		cursor.execute("UPDATE loans SET estrdate = '" + str(
			new_date) + "' WHERE isbn IS '" + isbn + "' AND userid IS '" + userid + "';")
		db.commit()
		await respond(ctx, ['Book renewed.'], admin=True)


@bot.command(name='ban', pass_context=True, hidden=True)
async def ban(ctx, userid: str):
	"""Ban a user from loaning any further books.

	Note: see 'unban' command to reverse this process.

	Args:
		ctx: Discord message context.
		userid: username of the user you want to ban.

	"""
	if await auth_check(ctx):
		cursor.execute("UPDATE users SET banned = TRUE WHERE userid IS '" + userid + "';")
		db.commit()
		await respond(ctx, ['User banned.'], admin=True)


@bot.command(name='unban', pass_context=True, hidden=True)
async def unban(ctx, userid: str):
	"""Unban a user.

	Note: opposite of 'ban' command.

	Args:
		ctx: Discord message context.
		userid: username of the user you want to unban.

	"""
	if await auth_check(ctx):
		cursor.execute("UPDATE users SET banned = FALSE WHERE userid IS '" + userid + "';")
		db.commit()
		await respond(ctx, ['User unbanned.'], admin=True)


@bot.command(name='add', pass_context=True, hidden=True)
async def add(ctx, isbn: str, count: int = 1):
	"""Add additional copies of a book.

	Args:
		ctx: Discord message context.
		isbn: ISBN of the book you want to include additional copies of.
		count: number of books you want to add.

	"""
	if await auth_check(ctx):
		cursor.execute("UPDATE books SET available = available + '" + str(count) + "' WHERE isbn IS '" + isbn + "';")
		db.commit()
		await respond(ctx, ['Book count modified.'], admin=True)


@bot.command(name='remove', pass_context=True, hidden=True)
async def remove(ctx, isbn: str, count: int = -1):
	"""Remove a copies of a book.

	Args:
		ctx: Discord message context.
		isbn: ISBN of the book you want to remove copies of.
		count: number of books you want to remove.

	"""
	await ctx.invoke(bot.get_command('add'), isbn=isbn, count=-abs(count))


@bot.command(name='delete', pass_context=True, hidden=True)
async def delete(ctx, isbn: str):
	"""Delete book(s) entirely from the library

	Args:
		ctx: Discord message context.
		isbn: ISBN of the book you want to delete.

	"""
	if await auth_check(ctx):
		cursor.execute(
			"UPDATE loans SET returned = TRUE, rdate = '" + str(datetime.datetime.now()) + "' WHERE isbn IS '" +
			isbn + "';")
		cursor.execute("DELETE FROM books WHERE isbn IS '" + isbn + "';")
		db.commit()
		await respond(ctx, ['Book deleted.'], admin=True)


@bot.command(name='return', pass_context=True, hidden=True)
async def return_(ctx, isbn: str, userid: int):
	"""Return a book to the library.

	Args:
		ctx: Discord message context.
		isbn: the ISBN of the book to return.
		userid: the ID of the user who had the book loaned.

	"""
	if await auth_check(ctx):
		cursor.execute("UPDATE books SET available = available + 1 WHERE isbn IS '" + isbn + "';")
		cursor.execute(
			"UPDATE loans SET returned = TRUE, rdate = '" + str(datetime.datetime.now()) + "' WHERE isbn IS '" +
			isbn + "' AND userid IS '" + str(userid) + "';")
		db.commit()
		await respond(ctx, ['Book successfully returned. It should now be available again to borrow.'])


async def respond(ctx, messages: list, dm: bool = False, admin: bool = False, fast: bool = False):
	"""Respond to a client by sending them messages.

	Send messages via the existing channel (default), DM or straight to the administrator.

	Note: 'admin' flag will always be sent over DM to administrator; no need for the 'dm' flag.

	Args:
		ctx: Discord message context.
		messages: a list of messages to send.
		dm: send via direct message rather than the existing channel.
		admin: send via direct message to the administrator instead.
		fast: send as quickly as possible without typing indicator.

	"""
	if admin is True:
		admin_user = await bot.fetch_user(int(ADMIN_USER))
		channel = await admin_user.create_dm()
	elif dm is True:
		channel = await ctx.message.author.create_dm()
	else:
		channel = ctx
	for message in messages:
		if fast is not True:
			async with channel.typing():
				await asyncio.sleep(0.5)
				await channel.send(message)
		else:
			await channel.send(message)


async def auth_check(ctx):
	"""Confirm if the user is authorised to call that command.

	Args:
		ctx: Discord message context.

	Returns:
		True for successful authorisation, False otherwise.

	"""
	user_id = ctx.message.author.id
	if user_id == int(ADMIN_USER):
		return True
	else:
		await respond(ctx, ['Authentication failure.'])
		user = await bot.fetch_user(user_id)
		await respond(ctx, ['Failed authentication attempt from: ' + str(user)], admin=True)
		return False


def format_book_records(books, display_due_details=False):
	"""Prettifies a list of book records for returning to the user.

		Args:
			books: books to format.
			display_due_details: include due notices in formatting.

		Returns:
			A list of books, ready for display back to the user.

	"""
	pretty_books = []
	remaining = 0
	for book in books:
		return_date = ''
		if display_due_details:
			return_date = book[1]
			remaining = int(book[2])
			book = book[0]
		if book[3] is not None:
			pretty_book = "**" + book[0] + "** *(" + book[3] + ")* by " + book[2] + " (" + str(book[5]) + ")"
		else:
			pretty_book = "**" + book[0] + "** by " + book[2] + " (" + str(book[5]) + ")"
		if display_due_details:
			pretty_book = pretty_book + "\nDue: " + str(return_date) + " "
			if remaining <= 0:
				pretty_book = pretty_book + " — **" + str(abs(remaining)) + " day(s) overdue!** " + random.choice(
					SAD_EMOJI) + "\n"
				pretty_book = pretty_book + "Please message <@" + str(
					ADMIN_USER) + '> or post in <#' + str(DISCORD_CHANNEL) + '> to renew the loan.\n'
			else:
				pretty_book = pretty_book + " — " + str(remaining) + " day(s) remaining " + random.choice(
					HAPPY_EMOJI) + "\n"
		else:
			pretty_book = pretty_book + " — https://www.amazon.co.uk/dp/s?k=" + str(book[5]) + "\n"
			if not book[4]:
				pretty_book = "~~" + pretty_book + "~~"
		pretty_books.append(pretty_book)
	return pretty_books


bot.run(TOKEN)
