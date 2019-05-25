# -*- coding: utf-8 -*-
import json
import logging
import time
import requests
from datetime import datetime, timedelta
from requests import ReadTimeout
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DingTalkAttendanceList(models.Model):
    _name = 'dingtalk.attendance.list'
    _description = '打卡列表'
    _rec_name = 'emp_id'

    TimeResult = [
        ('Normal', '正常'),
        ('Early', '早退'),
        ('Late', '迟到'),
        ('SeriousLate', '严重迟到'),
        ('Absenteeism', '旷工迟到'),
        ('NotSigned', '未打卡'),
    ]
    LocationResult = [
        ('Normal', '范围内'), ('Outside', '范围外'), ('NotSigned', '未打卡'),
    ]
    SourceType = [
        ('ATM', '考勤机'),
        ('BEACON', 'IBeacon'),
        ('DING_ATM', '钉钉考勤机'),
        ('USER', '用户打卡'),
        ('BOSS', '老板改签'),
        ('APPROVE', '审批系统'),
        ('SYSTEM', '考勤系统'),
        ('AUTO_CHECK', '自动打卡'),
    ]

    company_id = fields.Many2one(comodel_name='res.company', string=u'公司',
                                 default=lambda self: self.env.user.company_id.id)
    group_id = fields.Many2one(comodel_name='dingtalk.simple.groups', string=u'考勤组')
    din_id = fields.Char(string='唯一标识ID')
    recordId = fields.Char(string='打卡记录ID')
    workDate = fields.Date(string=u'工作日')
    emp_id = fields.Many2one(comodel_name='hr.employee', string=u'员工', required=True)
    checkType = fields.Selection(string=u'考勤类型', selection=[('OnDuty', '上班'), ('OffDuty', '下班')])
    timeResult = fields.Selection(string=u'时间结果', selection=TimeResult)
    locationResult = fields.Selection(string=u'位置结果', selection=LocationResult)
    baseCheckTime = fields.Datetime(string=u'基准时间')
    userCheckTime = fields.Datetime(string=u'实际打卡时间')
    sourceType = fields.Selection(string=u'数据来源', selection=SourceType)
    planId = fields.Char(string='排班ID')

    @api.model
    def get_attendance_list(self, start_date, end_date, user=None):
        """
        根据日期获取员工打卡信息，当user存在时将获取指定user的打卡，若不存在时，将获取所有员工的打卡信息，钉钉限制每次传递员工数最大为50个
        :param start_date:
        :param end_date:
        :param user:
        :return:
        """
        if not start_date and not end_date:
            raise UserError("必须选择要查询的开始日期和结束日期!")
        logging.info(">>>开始获取员工打卡信息...")
        user_list = list()
        if user:
            h_emp = self.env['hr.employee'].sudo().search([('name', '=', user)])
            if not h_emp:
                raise UserError("员工{}不存在！".format(user))
            for h in h_emp:
                if not h.din_id:
                    raise UserError("员工{}的钉钉ID无效,请输入其他员工或不填！".format(user))
                user_list.append(h.din_id)
        else:
            emps = self.env['hr.employee'].sudo().search([('din_id', '!=', '')])
            emp_len = len(emps)
            if emp_len <= 50:
                for e in emps:
                    user_list.append(e.din_id)
            elif emp_len > 50:
                n = 1
                e_list = list()
                for e in emps:
                    if n <= 50:
                        e_list.append(e.din_id)
                        n = n + 1
                    else:
                        user_list.append(e_list)
                        e_list = list()
                        e_list.append(e.din_id)
                        n = 2
                user_list.append(e_list)
        logging.info(user_list)
        for u in user_list:
            if isinstance(u, str):
                offset = 0
                limit = 50
                while True:
                    work_data_from = datetime.strptime(start_date, "%Y-%m-%d")
                    work_data_to = datetime.strptime(end_date, "%Y-%m-%d")
                    delta = timedelta(days=7)
                    work_data_to_mid = work_data_from + delta
                    while (work_data_from < work_data_to):
                        data = {
                            'workDateFrom': str(work_data_from),  # 开始日期
                            'workDateTo': str(work_data_to_mid),  # 结束日期
                            'userIdList': user_list,  # 员工列表
                            'offset': offset,
                            'limit': limit,
                        }
                        has_more = self.send_post_dingtalk(data)
                        work_data_from = work_data_to_mid
                        work_data_to_mid += delta
                    if not has_more:
                        break
                    else:
                        offset = offset + limit
                break
            elif isinstance(u, list):
                offset = 0
                limit = 50
                while True:
                    work_data_from = datetime.strptime(start_date, "%Y-%m-%d")
                    work_data_to = datetime.strptime(end_date, "%Y-%m-%d")
                    delta = timedelta(days=7)
                    work_data_to_mid = work_data_from + delta
                    while (work_data_from < work_data_to):
                        data = {
                            'workDateFrom': str(work_data_from),  # 开始日期
                            'workDateTo': str(work_data_to_mid),  # 结束日期
                            'userIdList': user_list,  # 员工列表
                            'offset': offset,  
                            'limit': limit, 
                        }
                        has_more = self.send_post_dingtalk(data)
                        work_data_from = work_data_to_mid
                        work_data_to_mid += delta
                    if not has_more:
                        break
                    else:
                        offset = offset + limit
        logging.info(">>>根据日期获取员工打卡结果信息结束...")
        return {'state': True, 'msg': '执行成功'}

    @api.model
    def send_post_dingtalk(self, data):
        url = self.env['ali.dingtalk.system.conf'].search([('key', '=', 'attendance_list')]).value
        token = self.env['ali.dingtalk.system.conf'].search([('key', '=', 'token')]).value
        headers = {'Content-Type': 'application/json'}
        try:
            result = requests.post(url="{}{}".format(url, token), headers=headers, data=json.dumps(data), timeout=15)
            result = json.loads(result.text)
            logging.info(result)
            if result.get('errcode') == 0:
                for rec in result.get('recordresult'):
                    data = {
                        'din_id': rec.get('id'),
                        'recordId': rec.get('recordId'),
                        'workDate': self.get_time_stamp(rec.get('workDate')),  # 工作日
                        'checkType': rec.get('checkType'),  # 考勤类型
                        'timeResult': rec.get('timeResult'),  # 时间结果
                        'locationResult': rec.get('locationResult'),  # 考勤类型
                        'baseCheckTime': self.get_time_stamp(rec.get('baseCheckTime')),  # 基准时间
                        'userCheckTime': self.get_time_stamp(rec.get('userCheckTime')),  # 实际打卡时间
                        'sourceType': rec.get('sourceType'),  # 数据来源
                        'planId': rec.get('planId'),
                    }
                    groups = self.env['dingtalk.simple.groups'].sudo().search([('group_id', '=', rec.get('groupId'))])
                    if groups:
                        data.update({'group_id': groups[0].id})
                    emp_id = self.env['hr.employee'].sudo().search([('din_id', '=', rec.get('userId'))])
                    if emp_id:
                        data.update({'emp_id': emp_id[0].id})
                    a_list = self.env['dingtalk.attendance.list'].search(
                        [('recordId', '=', rec.get('recordId')),('emp_id', '=',  emp_id[0].id),  ('baseCheckTime', '=', self.get_time_stamp(rec.get('baseCheckTime')))])
                    if a_list:
                        a_list.sudo().write(data)
                    else:
                        self.env['dingtalk.attendance.list'].sudo().create(data)
                if result.get('hasMore'):
                    return True
                else:
                    return False
            else:
                raise UserError('请求失败,原因为:{}'.format(result.get('errmsg')))
        except ReadTimeout:
            raise UserError("网络连接超时！")

    @api.model
    def get_time_stamp(self, timeNum):
        """
        将13位时间戳转换为时间
        :param timeNum:
        :return:
        """
        timeStamp = float(timeNum / 1000)
        timeArray = time.localtime(timeStamp)
        otherStyleTime = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
        return otherStyleTime