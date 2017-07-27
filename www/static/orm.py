#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'duke'

import asyncio,logging

import aiomysql

def log(sql,args=()):
    logging.info('SQL:%s' %sql) #打印出sql的日志

async def creat_pool(loop,**kw):#创建连接池
    logging.info('create database connection pool...')
    global __pool
    #初始化连接池参数
    __pool = await aiomysql.create_pool(
        host = kw.get('host', 'localhost'),
        port = kw.get('port', 3306),
        user = kw['user'],
        password = kw['password'],
        db = kw['db'],
        charset = kw.get('charset', 'utf-8'),
        autocommit = kw.get('autocommit', True),
        maxsize = kw.get('maxsize', 10),
        minsize = kw.get('minsize',1),
        loop = loop
    )

async def select(sql,args,size=None):#封装select操作函数
    log(sql,args)
    global __pool
    async with __pool.get() as conn:#创建一个结果为字典的游标
        async with conn.cursor(aiomysql.DictCursor) as cur:
        #如果指定数量，返回指定数量的记录，否则返回所有记录
            await cur.execute(sql.replace('?','%s'),args or())
            if size:
                rs = await cur.fetchmany(size)
            else:
                rs = await cur.fetchall()
            logging.info('rows returned: %s' % len(rs))
            return rs

async def execute(sql,args,autocommit=True):#Insert、Update、Delete操作的公共执行函数
    log(sql)
    async with __pool.get() as conn:
        if not autocommit:
            await conn.begin()
        try:
            #创建游标
            async with conn.cursor(aiomysql.DictCursor) as cur:
                #执行sql语句
                await cur.execute(sql.replace('?', '%s'),args)
                #获取操作的记录数
                affected = cur.rowcount
            if not autocommit:
                await conn.commit()
        except BaseException as e:
            if not autocommit:
                await conn.rollback()
            raise
        finally:
            conn.close()
        return affected

def creat_args_string(num):
    '''
    用来计算需要拼接多少占位符
    '''
    L = []
    for n in range(num):
        L.append('?')
    return ','.join(L)

class Field(object):

    def __init__(self,name,column_type,primary_key,default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' %(self.__class__.__name__,self.column_type,self.name)

class StringField(Field):

    def __init__(self,name=None,primary_key=False,default=None,ddl='varchar(100)'):
        super().__init__(name,ddl,primary_key,default)

class BooleanField(Field):

    def __init__(self,name=None,default=False):
        super().__init__(name,'boolean',False,default)
class IntegerField(Field):

    def __init__(self,name=None,primary_key=False,default=0):
        super().__init__(name,'bigint',primary_key,default)

class FloatField(Field):

    def __init__(self,name=None,primary_key=False,default=0.0):
        super().__init__(name,'real',primary_key,default)

class TextField(Field):

    def __init__(self,name=None,default=None):
        super().__init__(name,'text',False,default)

class ModelMetaclass(type):

    def __new__(cls,name,bases,attrs):
        if name=='Model':
            return type.__new__(cls,name,bases,attrs)
        tableName = attrs.get('__table__',None) or name
        logging.info('found model: %s (table: %s)' % (name,tableName))
        mappings = dict()
        fields = []
        primaryKey = None
        for k,v in attrs.items():
            if isinstance(v,Field):
                logging.info('  found mapping:%s ==>'%(k,v))
                mappings[k] = v
                if v.primary_key:
                    #找到主键
                    if primaryKey:
                        raise RuntimeError('Duplicate primary key for field: %s' %k)
                    primaryKey = k
                else:
                    fields.append(k)
            if not primaryKey:
                raise RuntimeError('Primary key not found.')
            for k in mappings.keys():#清空attrs
                attrs.pop(k)
            escaped_fields = list(map(lambda f: '`s`' %f,fields))
            #重新设置attrs
            attrs['__mappings__'] = mappings #保留属性和字段信息的映射关系
            attrs['__table__'] = tableName
            attrs['__primary_key__'] = primaryKey #主键属性名
            attrs['__fields__'] = fields #除主键外的属性名
            #构造默认的SELECT,INSERT,UPDATE和DELETE语句:
            attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
            attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (
            tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
            attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (
            tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
            attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
            return type.__new__(cls, name, bases, attrs)
class Model(dict,metaclass=ModelMetaclass):#定义所有ORM映射的基类

    def __init__(self,**kw):
        super(Model,self).__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self,key):
        return getattr(self,key,None)

    def getValueOrDefault(self,key):
        value = getattr(self,key,None)
        if value is None:#如果没有找到value
            field = self.__mappings__[key]#从mappings映射集合中找
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' %(key,str(value)))
                #using defalt value：使用默认值
                setattr(self,key,value)
        return value

@classmethod
async def findAll(cls,where=None,args=None,**kw):#往Model类添加class方法，让所有子类都调用
    #find objects by where clause.
    '''
    通过where查找多条记录对象
    param where:where查询条件
    param args:sql参数
    param kw: 查询条件列表
    return:多条记录集合
    '''
    sql = [cls.__select__]
    #如果where查询条件存在
    if where:
        sql.append('where')         #添加where关键字
        sql.append(where)           #拼接where查询条件
    if args is None:
        args = []
    orderBy = kw.get('orderBy',None)    #获取kw里面的orderby查询条件
    if orderBy:                         #如果存在orderby
        sql.append('order by')          #拼接orderBy字符串
        sql.append(orderBy)             #拼接orderBy查询条件

    limit = kw.get('limit',None)        #获取limit查询条件
    if limit is not None:
        sql.append('limit')
        if isinstance(limit,int):       #如果limit是int类型
            sql.append('?')             #sql拼接一个占位符
            args.append(limit)          #将limit调价进参赛列表，之所以添加
                                        #参数列表之后再进行整合是为了防止sql注入
        elif isinstance(limit,tuple) and len(limit) == 2:#如果limit是一个tuple类型且长度为2
            sql.append('?,?')            #sql语句拼接两个占位符
            args.extend(limit)          #将limit添加进参数列表
        else:
            raise ValueError('Invalid limit value: %s' % str(limit))
    rs = await select(''.join(sql),args)#将args参赛列表注入sql语句之后，传递给select函数进行查询并返回查询结果
    return [cls(**r) for r in rs]

@classmethod
async def findNumber(cls,selectField,where=None,args=None):
    'find number by select and where.'#查询某个字段的数量
    sql = ['select %s _num_ from `%s`' % (selectField,cls.__table__)]
    if where:
        sql.append('where')
        sql.append(where)
    rs = await select(''.join(sql),args,1)
    if len(rs) == 0:
        return None
    return rs[0]['_num_']

@classmethod
async def findById(cls,pk):
    '''通过id查询
    param pk:id
    return:一条记录
    '''
    'find object by primary key.'#通过主键查找对象,即通过ID查询
    rs = await select('%s where `%s`=?' %(cls.__select__,cls.__primary_key__),[pk],1)
    if len(rs) == 0:
        return None
    return cls(**rs[0])

@classmethod
async def findByColum(cls,k,ck):
    '''
    通过指定字段查询
    param k:要查询的字段
    param ck:查询字段对应的值
    return:一条记录
    '''
    fi = None
    for field in cls.__fields__:        #遍历属性列表看有没有这个属性
        if k == field:                  #找到了就赋值给fi然后退出循环
            fi=field
            break
    if fi is None:
        raise AttributeError('The field was not found in %s：' % cls.__table__)

    rs = await select('%s where `s`=?' % (cls.__select__,fi), [ck],1)
    if len(rs) == 0:
        return None
    return cls(**rs[0])

async def save(self):#保存
    #将__fields__保存的除主键外的所有属性一次传递到getValueOrDefault函数中获取值
    args = list(map(self.getValueOrDefault,slef.__fields__))
    #获取主键值
    args.append(self.getValueOrDefault(self.__primary_key__))
    #执行insert sql语句
    rows = await execute(self.__insert__,args)
    if rows !=1:
        logging.warning('failed to insert record:affected rows: %s' % rows)

async def update(self):#更新记录
    args = list(map(self.getValue,self.__fields__))
    args.append(self.getValue(self.__primary_key__))
    rows = await execute(self.__update__,args)
    if rows !=1:
        logging.warning('failed to update by primary key: affected rows: %s' % rows)

async def remove(self):#删除记录
    args = [self.getValue(self.__primary_key__)]
    rows = await execute(self.__delete__,args)
    if rows !=1:
        logging.warning('failed to remove by primary key: affected rows: %s' % rows)