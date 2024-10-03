# -*- coding: utf-8 -*-
# @Author  : HBcao
# @Email   : hbcaoqaq@gmail.com
# @Info    : pixiv爬取
""".env.example
# pixiv cookie中的 PHPSESSID
pixiv_PHPSESSID =
"""

from telethon import types, events, errors, Button
import re
import asyncio

import util
from util.log import logger
from util.progress import Progress
from plugin import handler
from .data_source import PixivClient, parse_msg, get_telegraph
import filters


cmd_header_pattern = re.compile(r'/?pid(?:@%s)' % bot.me.username)
_p = (
  r'(?:^|^(?:/?pid(?:@%s)?) ?|(?:https?://)?(?:www\.)?(?:pixiv\.net/(?:member_illust\.php\?.*illust_id=|artworks/|i/)))(\d{6,12})(?:[^0-9].*)?$|^/pid.*$'
  % bot.me.username
)
_pattern = re.compile(_p).search
_group_pattern = re.compile(_p.replace(r'(?:^|', r'^(?:')).search


@handler(
  'pid',
  pattern=_pattern,
  info='获取p站作品 /pid <url/pid> [hide] [mark]',
  filter=(
    filters.PRIVATE
    | filters.Filter(lambda event: _group_pattern(event.message.message))
  )
  & ~(filters.PHOTO | filters.VIDEO),
)
async def _pixiv(event, text):
  text = cmd_header_pattern.sub('', text).strip()
  match = _pattern(text)
  if match is None or not (pid := match.group(1)):
    return await event.reply(
      '用法: /pid <url/pid> [options]\n'
      '获取p站图片\n'
      '- <url/pid>: p站链接或pid\n'
      '- [hide/省略]: 省略图片说明\n'
      '- [mark/遮罩]: 给图片添加遮罩\n'
      '- [origin/原图]: 发送原图\n'
    )

  options = util.string.Options(
    text, hide=('简略', '省略'), mark=('spoiler', '遮罩'), origin='原图'
  )
  logger.info(f'{pid = }, {options = }')

  mid = await event.reply('请等待...')

  async def send_animation():
    nonlocal mid
    async with bot.action(event.peer_id, 'file'):
      data = util.Animations()
      await mid.edit('生成动图中...')
      if not (file := data[pid]):
        file = await client.get_anime()
        if not file:
          return await event.reply('生成动图失败')

      bar = Progress(mid, prefix='上传中...')
      res = await bot.send_file(
        event.peer_id,
        file,
        reply_to=event.message,
        caption=msg,
        parse_mode='html',
        force_document=False,
        attributes=[types.DocumentAttributeAnimated()],
        progress_callback=bar.update,
      )
      with data:
        data[pid] = res
      await mid.delete()

  async def send_photos():
    nonlocal mid
    pid = res['illustId']
    imgUrl = res['urls']['original']
    data = util.Documents() if options.origin else util.Photos()
    bar = Progress(
      mid,
      total=count,
      prefix=f'正在获取 p1 ~ {count}',
    )

    async def get_img(i):
      nonlocal data
      url = imgUrl.replace('_p0', f'_p{i}')
      key = f'{pid}_p{i}'
      if file_id := data[key]:
        return util.media.file_id_to_media(file_id, options.mark)

      try:
        img = await client.getImg(url, saveas=key, ext=True)
      except Exception:
        logger.error(f'p{i} 图片获取失败', exc_info=1)
        return await mid.edit(f'p{i} 图片获取失败')
      await bar.add(1)
      return await util.media.file_to_media(
        img,
        options.mark,
        force_document=options.origin,
      )

    tasks = [get_img(i) for i in range(count)]
    result = await asyncio.gather(*tasks)
    async with bot.action(event.peer_id, 'photo'):
      m = await bot.send_file(
        event.peer_id, result, caption=msg, parse_mode='html', reply_to=event.message
      )

    with data:
      for i in range(count):
        key = f'{pid}_p{i}'
        data[key] = m[i]
    await mid.delete()
    return m

  async with PixivClient(pid) as client:
    res = await client.get_pixiv()
    if isinstance(res, str):
      return await mid.edit(res)
    msg, tags = parse_msg(res, options.hide)
    if res['illustType'] == 2:
      return await send_animation()

    count = res['pageCount']
    if count <= 10:
      res = await send_photos()
    else:
      url, msg = await get_telegraph(res, tags)
      await mid.delete()
      return await bot.send_file(
        event.peer_id,
        caption=msg,
        parse_mode='HTML',
        file=types.InputMediaWebPage(
          url=url,
          force_large_media=True,
          optional=True,
        ),
        reply_to=event.message,
      )

  if options.origin:
    return

  message_id_bytes = res[0].id.to_bytes(4, 'big')
  sender_bytes = b'~' + event.sender_id.to_bytes(6, 'big', signed=True)
  pid_bytes = int(pid).to_bytes(4, 'big')
  await event.reply(
    '获取完成',
    buttons=[
      [
        Button.inline(
          '移除遮罩' if options.mark else '添加遮罩',
          b'mark_' + message_id_bytes + sender_bytes,
        ),
        Button.inline(
          '详细描述' if options.hide else '简略描述',
          b'pid_' + message_id_bytes + b'_' + pid_bytes + sender_bytes,
        ),
      ],
      [Button.inline('获取原图', b'pidori_' + pid_bytes)],
      [Button.inline('关闭面板', b'delete' + sender_bytes)],
    ],
  )


_button_pattern = re.compile(
  rb'pid_([\x00-\xff]{4,4})_([\x00-\xff]{4,4})(?:~([\x00-\xff]{6,6}))?$'
).match


@bot.on(events.CallbackQuery(pattern=_button_pattern))
async def _(event):
  """
  简略描述/详细描述 按钮回调
  """
  peer = event.query.peer
  match = event.pattern_match
  message_id = int.from_bytes(match.group(1), 'big')
  pid = int.from_bytes(match.group(2), 'big')
  sender_id = None
  if t := match.group(3):
    sender_id = int.from_bytes(t, 'big')
  # logger.info(f'{message_id=}, {pid=}, {sender_id=}, {event.sender_id=}')

  if sender_id and event.sender_id and sender_id != event.sender_id:
    participant = await bot.get_permissions(peer, event.sender_id)
    if not participant.delete_messages:
      return await event.answer('只有消息发送者可以修改', alert=True)

  message = await bot.get_messages(peer, ids=message_id)
  if message is None:
    return await event.answer('消息被删除', alert=True)

  hide = any(isinstance(i, types.MessageEntityBlockquote) for i in message.entities)

  async with PixivClient(pid) as client:
    res = await client.get_pixiv()
  if isinstance(res, str):
    return await event.answer(res, alert=True)
  msg, tags = parse_msg(res, hide)
  try:
    await message.edit(msg, parse_mode='html')
  except errors.MessageNotModifiedError:
    logger.warning('MessageNotModifiedError')

  message = await event.get_message()
  buttons = message.buttons
  text = '详细描述' if hide else '简略描述'
  index = 0
  for i, ai in enumerate(buttons[0]):
    if _button_pattern(ai.data):
      index = i
      data = ai.data
      break
  buttons[0][index] = Button.inline(text, data)

  try:
    await event.edit(buttons=buttons)
  except errors.MessageNotModifiedError:
    logger.warning('MessageNotModifiedError')
  await event.answer()


_ori_pattern = re.compile(rb'pidori_([\x00-\xff]{4,4})$').match


@bot.on(events.CallbackQuery(pattern=_ori_pattern))
async def _(event):
  """
  获取原图按钮回调
  """
  peer = event.query.peer
  match = event.pattern_match
  pid = int.from_bytes(match.group(1), 'big')
  message = await event.get_message()
  buttons = message.buttons
  buttons.pop(1)
  try:
    await event.edit(buttons=buttons)
  except errors.MessageNotModifiedError:
    logger.warning('MessageNotModifiedError')

  hide = ''
  for i in buttons[0]:
    if i.text == '详细描述':
      hide = 'hide'
      break
    if i.text == '简略描述':
      break

  await event.answer()
  event.message = message
  event.peer_id = peer
  text = f'/pid {pid} origin {hide}'
  event.pattern_match = _pattern(text)
  await _pixiv(event, text)
