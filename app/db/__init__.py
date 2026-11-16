"""The database: one SQLite file, and everything that is true of all of it.

The library is in git because it is authored and reviewed. What is not --
the BOMs people upload, who is signed in -- is kept here. Each thing that is
kept has a store of its own, next to the code that uses it (`app.boms.store`,
`app.auth.sessions`), and none of them opens the file itself: they are handed
a `Database` and ask it for a connection.

    database    where the file is, how it is opened, what happens if it cannot be
    migrations  its tables, as the numbered steps that made them
    backup      copies of it, because it cannot be rebuilt from anywhere else

To keep something new: add a migration that makes its table, and write a
store that takes a `Database`.
"""
