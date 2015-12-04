from unittest import TestCase
from itertools import chain
from collections import defaultdict

from sqlalchemy import create_engine, select
from sqlalchemy.types import Integer, Unicode
from sqlalchemy.schema import MetaData, Table, Column, ForeignKey

from hiku.store import Store
from hiku.graph import Field, Edge, Link


metadata = MetaData()


foo_table = Table(
    'foo',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', Unicode),
    Column('count', Integer),
    Column('bar_id', ForeignKey('bar.id')),
)


bar_table = Table(
    'bar',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', Unicode),
    Column('type', Integer),
)


def _query(engine, table, attrs, idents):
    p_key, = list(table.primary_key)
    columns = [getattr(table.c, attr) for attr in attrs]
    rows = engine.execute(select([p_key] + columns)
                          .where(p_key.in_(idents))).fetchall()
    rows_map = {row[p_key]: [row[k] for k in columns] for row in rows}
    return [rows_map.get(ident) for ident in idents]


def query_foo(engine, attrs, idents):
    return _query(engine, foo_table, attrs, idents)


def query_bar(engine, attrs, idents):
    return _query(engine, bar_table, attrs, idents)


def foo_bar_link(engine, bar_ids):
    return bar_ids


def bar_foo_link(engine, bar_ids):
    rows = engine.execute(select([foo_table.c.id, foo_table.c.bar_id])
                          .where(foo_table.c.bar_id.in_(bar_ids))).fetchall()
    mapping = defaultdict(list)
    for row in rows:
        mapping[row.bar_id].append(row.id)
    return [mapping[bar_id] for bar_id in bar_ids]


foo_edge = Edge('foo', {
    'id': Field('id', query_foo),
    'name': Field('name', query_foo),
    'count': Field('count', query_foo),
    'bar_id': Field('bar_id', query_foo),

    'bar': Link('bar_id', 'bar', foo_bar_link, False),
})

bar_edge = Edge('bar', {
    'id': Field('id', query_bar),
    'name': Field('name', query_bar),
    'type': Field('type', query_bar),

    'foo_list': Link('id', 'foo', bar_foo_link, True),
})


def query_edge(engine, store, edge, fields, ids):
    mapping = defaultdict(list)
    for field_name in fields:
        field = edge.fields[field_name]
        if isinstance(field, Link):
            field = edge.fields[field.requires]
        mapping[field.func].append(field.name)

    for func, names in mapping.items():
        rows = func(engine, names, ids)
        for row_id, row in zip(ids, rows):
            store.update(edge.name, row_id, zip(names, row))


def query_link(engine, store, edge, link_name, ids):
    link = edge.fields[link_name]
    if link.is_list:
        from_ids = [store.ref(edge.name, i)[link.requires] for i in ids]
        to_ids_list = link.func(engine, from_ids)
        for from_id, to_ids in zip(from_ids, to_ids_list):
            store.update(edge.name, from_id,
                         [(link_name, [store.ref(link.entity, i)
                                       for i in to_ids])])
        return list(chain.from_iterable(to_ids_list))
    else:
        from_ids = [store.ref(edge.name, i)[link.requires] for i in ids]
        to_ids = link.func(engine, from_ids)
        for from_id, to_id in zip(from_ids, to_ids):
            store.update(edge.name, from_id,
                         [(link_name, store.ref(link.entity, to_id))])
        return to_ids


class TestSourceSQL(TestCase):

    def setUp(self):
        self.engine = e = create_engine('sqlite://')
        metadata.create_all(e)

        bar_insert = lambda r: e.execute(bar_table.insert(), r).lastrowid
        self.bar_ids = list(map(bar_insert, [
            {'name': 'bar1', 'type': 1},
            {'name': 'bar2', 'type': 2},
            {'name': 'bar3', 'type': 3},
        ]))

        foo_insert = lambda r: e.execute(foo_table.insert(), r).lastrowid
        list(map(foo_insert, [
            {'name': 'foo1', 'count': 5, 'bar_id': self.bar_ids[0]},
            {'name': 'foo2', 'count': 10, 'bar_id': self.bar_ids[1]},
            {'name': 'foo3', 'count': 15, 'bar_id': self.bar_ids[2]},
        ]))

    def test_m2o(self):
        foo_ids = [3, 2, 1]
        store = Store()

        query_edge(self.engine, store, foo_edge,
                   ['name', 'count', 'bar'], foo_ids)

        bar_ids = query_link(self.engine, store, foo_edge, 'bar', foo_ids)

        query_edge(self.engine, store, bar_edge,
                   ['name', 'type'], bar_ids)

        self.assertEqual(
            [store.ref('foo', i) for i in [3, 2, 1]],
            [
                {'name': 'foo3', 'count': 15, 'bar_id': 3,
                 'bar': {'name': 'bar3', 'type': 3}},
                {'name': 'foo2', 'count': 10, 'bar_id': 2,
                 'bar': {'name': 'bar2', 'type': 2}},
                {'name': 'foo1', 'count': 5, 'bar_id': 1,
                 'bar': {'name': 'bar1', 'type': 1}},
            ]
        )

    def test_o2m(self):
        bar_ids = [3, 2, 1]
        store = Store()

        query_edge(self.engine, store, bar_edge,
                   ['name', 'type', 'foo_list'], bar_ids)

        foo_ids = query_link(self.engine, store, bar_edge, 'foo_list', bar_ids)

        query_edge(self.engine, store, foo_edge,
                   ['name', 'count'], foo_ids)

        self.assertEqual(
            [store.ref('bar', i) for i in [3, 2, 1]],
            [
                {'id': 3, 'name': 'bar3', 'type': 3, 'foo_list': [
                    {'name': 'foo3', 'count': 15},
                ]},
                {'id': 2, 'name': 'bar2', 'type': 2, 'foo_list': [
                    {'name': 'foo2', 'count': 10},
                ]},
                {'id': 1, 'name': 'bar1', 'type': 1, 'foo_list': [
                    {'name': 'foo1', 'count': 5},
                ]},
            ],
        )
