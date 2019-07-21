# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import quote

import requests
import werkzeug.urls
import werkzeug.utils
from requests import ReadTimeout
from werkzeug.exceptions import BadRequest

from odoo import http, tools
from odoo.addons.ali_dindin.dingtalk.main import client
from odoo.addons.auth_oauth.controllers.main import \
    OAuthController as Controller
from odoo.addons.auth_oauth.controllers.main import OAuthLogin as Home
from odoo.addons.web.controllers.main import ensure_db
from odoo.exceptions import AccessDenied
from odoo.http import request
from odoo.tools import pycompat

_logger = logging.getLogger(__name__)


class OAuthLogin(Home):
    def list_providers(self):
        result = super(OAuthLogin, self).list_providers()

        for provider in result:
            if 'dingtalk' in provider['auth_endpoint']:
                return_url = request.httprequest.url_root + 'dingtalk/auth_oauth/signin/' + str(provider['id'])
                state = self.get_state(provider)
                params = dict(
                    response_type='code',
                    appid=provider['client_id'],
                    redirect_uri=return_url,
                    scope=provider['scope'],
                    state='STATE',
                )
                provider['auth_link'] = "%s?%s" % (provider['auth_endpoint'], werkzeug.urls.url_encode(params))

        return result

    @http.route('/web', type='http', auth="none")
    def web_client(self, s_action=None, **kw):
        ensure_db()
        user_agent = request.httprequest.user_agent.string.lower()

        if not request.session.uid and 'dingtalk' in user_agent:
            providers = request.env['auth.oauth.provider'].sudo().search([('auth_endpoint', 'ilike', 'dingtalk')])
            if not providers:
                return super(OAuthLogin, self).web_client(s_action, **kw)
            provider_id = providers[0].id

            icp = request.env['ir.config_parameter'].sudo()
            appid = icp.get_param('ali_dindin.din_login_appid')
            # 应用内免登
            # 构造如下跳转链接，此链接处理成功后，会重定向跳转到指定的redirect_uri，并向url追加临时授权码code及state两个参数。
            url = "https://oapi.dingtalk.com/connect/oauth2/sns_authorize?appid=%s&response_type=code&scope=snsapi_auth&state=STATE&redirect_uri=" % (
                appid)
            return self.redirect_dingtalk(url, provider_id)
        else:
            return super(OAuthLogin, self).web_client(s_action, **kw)

    def redirect_dingtalk(self, url, provider_id):
        url = pycompat.to_text(url).strip()
        if werkzeug.urls.url_parse(url, scheme='http').scheme not in ('http', 'https'):
            url = u'http://' + url
        url = url.replace("'", "%27").replace("<", "%3C")

        redir_url = "encodeURIComponent('%sdingtalk/auth_oauth/signin/%d' + location.hash.replace('#','?'))" % (
            request.httprequest.url_root, provider_id)
        return "<html><head><script>window.location = '%s' +%s;</script></head></html>" % (url, redir_url)


class OAuthController(Controller):

    @http.route('/dingding/auto/login/in', type='http', auth='none')
    def dingding_auto_login(self, **kw):
        """
        免登入口
        :param kw:
        :return:
        """
        logging.info(">>>用户正在使用免登...")
        # data = {'corp_id': request.env['ir.config_parameter'].sudo(
        # ).get_param('ali_dindin.din_corpid')}
        data = {'corp_id': tools.config.get('din_corpid', '')}
        return request.render('auth_oauth_dingtalk.dingding_auto_login', data)

    @http.route('/dingding/auto/login', type='http', auth='none')
    def auth(self, **kw):
        """
        通过得到的【免登授权码】获取用户信息
        :param kw:
        :return:
        """
        authCode = kw.get('authcode')
        if authCode:
            get_result = self.get_user_info_by_auth_code(authCode)
            if not get_result.get('state'):
                return self._post_error_message(get_result.get('msg'))
            userid = get_result.get('userid')
            logging.info(">>>获取的user_id为：%s", userid)
            if userid:
                employee = request.env['hr.employee'].sudo().search(
                    [('din_id', '=', userid)])
                if employee:
                    user = employee.user_id
                    if user:
                        # 解密钉钉登录密码
                        logging.info(u'>>>:解密钉钉登录密码')
                        password = base64.b64decode(user.din_password)
                        password = password.decode(
                            encoding='utf-8', errors='strict')
                        request.session.authenticate(
                            request.session.db, user.login, password)
                        return http.local_redirect('/web')
                    else:
                        # 自动注册
                        import random
                        password = str(random.randint(100000, 999999))
                        fail = request.env['res.users'].sudo(
                        ).create_user_by_employee(employee.id, password)
                        if not fail:
                            return http.local_redirect('/dingding/auto/login/in')
                    return http.local_redirect('/web/login')
                return http.local_redirect('/web/login')
        else:
            return self._post_error_message("获取临时授权码失败,请检查钉钉开发者后台设置!")


    @http.route('/dingtalk/auth_oauth/signin/<int:provider_id>', type='http', auth='none')
    def signin(self, provider_id, **kw):

        code = kw.get('code', "")
        _logger.info("获得的code: %s", code)
        userinfo = self.get_userid_by_unionid(code)
        userid = client.user.get_userid_by_unionid(userinfo['unionid']).get('userid')
        try:
            _logger.info("track...........")
            _logger.info("cre:%s:%s", str(provider_id), str(userid))
            credentials = request.env['res.users'].sudo().auth_oauth_dingtalk(provider_id, userid)
            _logger.info("credentials: %s", credentials)
            url = '/web'
            hash = ""
            for key in kw.keys():
                if key not in ['code', 'state']:
                    hash += '%s=%s&' % (key, kw.get(key, ""))
            if hash:
                hash = hash[:-1]
                url = '/web#' + hash
            uid = request.session.authenticate(*credentials)
            if uid is not False:
                request.params['login_success'] = True
                return http.redirect_with_hash(url)
        except AttributeError:
            url = "/web/login?oauth_error=1"
        except AccessDenied:
            # oauth credentials not valid, user could be on a temporary session
            _logger.info(
                'OAuth2: access denied, redirect to main page in case a valid session exists, without setting cookies')
            url = "/web/login?oauth_error=3"
            redirect = werkzeug.utils.redirect(url, 303)
            redirect.autocorrect_location_header = False
            return redirect
        except Exception as e:
            # signup error
            _logger.exception("OAuth2: %s", str(e))
            url = "/web/login?oauth_error=2"

    def get_userid_by_unionid(self, tmp_auth_code):
        """
        根据返回的【临时授权码】获取用户信息
        :param code:
        :return:
        """
        url = request.env['ali.dindin.system.conf'].sudo().search(
            [('key', '=', 'getuserinfo_bycode')]).value
        login_appid = request.env['ir.config_parameter'].sudo(
        ).get_param('ali_dindin.din_login_appid')
        key = request.env['ir.config_parameter'].sudo(
        ).get_param('ali_dindin.din_login_appsecret')
        # def current_milli_time(): return int(round(time.time() * 1000))
        # msg = str(current_milli_time())
        msg = pycompat.to_text(int(time.time() * 1000))
        _logger.info("时间戳:%s", msg)
        # ------------------------
        # 签名
        # ------------------------
        signature = hmac.new(key.encode('utf-8'), msg.encode('utf-8'),
                             hashlib.sha256).digest()
        signature = quote(base64.b64encode(signature), 'utf-8')
        data = {
            'tmp_auth_code': tmp_auth_code
        }
        headers = {'Content-Type': 'application/json'}
        new_url = "{}signature={}&timestamp={}&accessKey={}".format(
            url, signature, msg, login_appid)
        _logger.info("new_url:%s", new_url)
        try:
            result = requests.post(
                url=new_url, headers=headers, data=json.dumps(data), timeout=15)
            result = json.loads(result.text)
            logging.info(">>>钉钉登录获取用户信息返回结果%s", result)
            if result.get('errcode') == 0:
                return result.get('user_info')
            raise BadRequest(result)

        except ReadTimeout:
            return {'state': False, 'msg': '网络连接超时'}

    def get_user_info_by_auth_code(self, auth_code):
        """
        根据返回的【免登授权码】获取用户信息
        :param auth_code:
        :return:
        """
        try:
            result = client.user.getuserinfo(auth_code)
            logging.info(">>>获取用户信息返回结果:%s", result)
            if result.get('errcode') != 0:
                return {'state': False, 'msg': "钉钉接口错误:{}".format(result.get('errmsg'))}
            return {'state': True, 'userid': result.get('userid')}
        except Exception as e:
            return {'state': False, 'msg': "登录失败,异常信息:{}".format(str(e))}