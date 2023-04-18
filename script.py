from threading import Thread, Lock
from time import sleep
from pathlib import Path
import json
import yaml
from os import listdir
from os.path import exists
from copy import deepcopy
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, Filters, CommandHandler, MessageHandler, CallbackQueryHandler, Updater
from modules.text_generation import generate_reply

params = {
    "token": "TELEGRAM_TOKEN",  # Telegram bot token! Ask https://t.me/BotFather to get!
    'bot_mode': "chat",  # chat, chat-restricted, notebook
    # delete (remove cut text) or cross (cross cut text)
    'cutoff_mode': "delete",
    # character json file from text-generation-webui/characters
    'character_to_load': "Example.yaml",
}


class TelegramBotWrapper:
    # Default error messages
    GENERATOR_FAIL = "<GENERATION FAIL>"
    GENERATOR_EMPTY_ANSWER = "<EMPTY ANSWER>"
    UNKNOWN_TEMPLATE = "<UNKNOWN TEMPLATE>"
    # Various predefined data
    CUTOFF_DELETE = "delete"
    CUTOFF_STRICT = "cross"
    BTN_CONTINUE = 'Continue'
    BTN_REGEN = 'Regen'
    BTN_CUTOFF = 'Cutoff'
    BTN_RESET = 'Reset'
    BTN_DOWNLOAD = 'Download'
    BTN_CHAR_LIST = 'Chars'
    # Supplementary structure
    # dict of User data dicts, here placed all users' session info.
    users: dict = {}
    # Internal, changeable settings
    impersonate_prefix = "#"  # Prefix for "impersonate" messages during chatting
    default_users_data = {  # data template for user. if no default char or default char file - use this as main.
        "name1": "You",  # username
        "name2": "Bot",  # bot name
        "context": "",  # context of conversation, example: "Conversation between Bot and You"
        "user_in": [],  # "user input history": [["Hi!","Who are you?"]], need for regenerate option
        "history": [],  # "history": [["Hi!", "Hi there!","Who are you?", "I am you assistant."]],
        "msg_id": [],  # "msg_id": [143, 144, 145, 146],
        "greeting": 'Hi',  # just greeting message from bot
    }
    default_messages_template = {  # dict of messages templates for various situations. Use _VAR_ replacement
        # When button refers to non-existing data
        "mem_lost": "\n<MEMORY LOST!>\nSend /start or any text for new session.",
        # added when "regenerate button" working
        "retyping": "<i>\n_NAME2_ retyping...</i>",
        "typing": "<i>\n_NAME2_ typing...</i>",  # added when generating working
        "char_loaded": "<CHARACTER _NAME2_ LOADED!>\n_GREETING_.",  # When new char loaded
        # When history cleared
        "mem_reset": "<MEMORY RESET!>\nSend /start or any text for new session.",
        # New conversation started (not used now)
        "start": "<CHARACTER _NAME2_ LOADED>\nSend /start or message.",
        "hist_to_chat": "To load history - forward message to this chat",  # download history
        "hist_loaded": "<_NAME2_ LOADED>\n_GREETING_\n\n<LAST MESSAGE:>\n_CUSTOM_STRING_",  # load history
    }
    generation_params = {
        'max_new_tokens': 200,
        'seed': -1.0,
        'temperature': 0.72,
        'top_p': 0.73,
        'top_k': 0,
        'typical_p': 1,
        'repetition_penalty': 1.18,
        'encoder_repetition_penalty': 1,
        'no_repeat_ngram_size': 0,
        'min_length': 0,
        'do_sample': True,
        'penalty_alpha': 0,
        'num_beams': 1,
        'length_penalty': 1,
        'early_stopping': False,
        'add_bos_token': True,
        'ban_eos_token': False,
        'truncation_length': 1024,
        'custom_stopping_strings': [],
        'end_of_turn': '',
        'chat_prompt_size': 1024,
        'chat_generation_attempts': 1,
        'stop_at_newline': False,
        'skip_special_tokens': True,
    }

    def __init__(self,
                 bot_mode="chat",  # bot mode - chat, chat-restricted, notebook
                 characters_dir_path="characters",  # there stored characters files
                 # delete (remove cut text) or cross (cross cut text)
                 cutoff_mode="delete",
                 default_char_json="Example.json",  # name of default char.json file
                 history_dir_path="extensions/telegram_bot/history",  # there stored users history
                 # there stored tg token
                 default_token_file_path="extensions/telegram_bot/telegram_token.txt",
                 ):
        """
        Init telegram bot class. Use run_telegram_bot() to initiate bot.
        :param bot_mode: bot mode (chat, chat-restricted, notebook). Default is "chat".
        :param characters_dir_path: place where stored characters .json files. Default is "chat".
        :param default_char_json: name of default character.json file. Default is "chat".
        :param history_dir_path: place where stored chat history. Default is "extensions/telegram_bot/history".
        :param default_token_file_path: path to token file. Default is "extensions/telegram_bot/telegram_token.txt".
        :return: None
        """
        # Set paths to history, default token file, characters dir
        self.history_dir_path = history_dir_path
        self.default_token_file_path = default_token_file_path
        self.characters_dir_path = characters_dir_path
        # Set bot mode
        self.bot_mode = bot_mode
        # Set default character json file
        self.default_char_json = default_char_json
        # Set cutoff mode
        self.cutoff_mode = cutoff_mode
        # Set load command
        self.load_cmd = "load"
        # Set buttons
        self.button_start = None
        if self.bot_mode == "chat":
            self.button = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="▶Continue", callback_data=self.BTN_CONTINUE),
                        InlineKeyboardButton(
                            text="🔄Regenerate", callback_data=self.BTN_REGEN),
                        InlineKeyboardButton(
                            text="✂Cutoff", callback_data=self.BTN_CUTOFF),
                        InlineKeyboardButton(
                            text="🚫Reset", callback_data=self.BTN_RESET),
                        InlineKeyboardButton(
                            text="💾Download", callback_data=self.BTN_DOWNLOAD),
                        InlineKeyboardButton(
                            text="🎭Chars", callback_data=self.BTN_CHAR_LIST),
                    ]
                ]
            )
        elif self.bot_mode == "chat-restricted":
            self.button = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="▶Continue", callback_data=self.BTN_CONTINUE),
                        InlineKeyboardButton(
                            text="🔄Regenerate", callback_data=self.BTN_REGEN),
                        InlineKeyboardButton(
                            text="✂Cutoff", callback_data=self.BTN_CUTOFF),
                        InlineKeyboardButton(
                            text="🚫Reset memory", callback_data=self.BTN_RESET),
                    ]
                ]
            )
        elif self.bot_mode == "notebook":
            self.button = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="▶Continue", callback_data=self.BTN_CONTINUE),
                        InlineKeyboardButton(
                            text="🚫Reset memory", callback_data=self.BTN_RESET),
                    ]
                ]
            )
        # Set dummy obj for telegram updater
        self.updater = None
        # Define generator lock to prevent GPU overloading
        self.generator_lock = Lock()

    # =============================================================================
    # Run bot with token! Initiate updater obj!
    def run_telegram_bot(self, bot_token=None, token_file_name=None):
        """
        Start the Telegram bot.

        :param bot_token: (str) The Telegram bot token. If not provided, try to read it from `token_file_name`.
        :param token_file_name: (str) The name of the file containing the bot token. Default is `None`.
        """
        if not bot_token:
            token_file_name = token_file_name or self.default_token_file_path
            with open(token_file_name, "r", encoding="utf-8") as f:
                bot_token = f.read().strip()

        self.updater = Updater(token=bot_token, use_context=True)
        self.updater.dispatcher.add_handler(
            CommandHandler(["start", "reset"], self.cb_get_command))
        self.updater.dispatcher.add_handler(
            MessageHandler(Filters.text, self.cb_get_message))
        self.updater.dispatcher.add_handler(MessageHandler(
            Filters.document.mime_type("application/json"), self.cb_get_document))
        self.updater.dispatcher.add_handler(
            CallbackQueryHandler(self.cb_opt_button))

        self.updater.start_polling()
        print("Telegram bot started!", self.updater)

    # =============================================================================
    # Handlers
    def cb_get_command(self, upd, context):
        message_text = upd.message.text
        if message_text == "/start":
            Thread(target=self.send_welcome_message,
                   args=(upd, context)).start()

    def cb_get_message(self, upd, context):
        message_text = upd.message.text
        if message_text.startswith(f"/{self.load_cmd}") and self.bot_mode != "chat-restricted":
            Thread(target=self.load_new_character, args=(upd, context)).start()
        else:
            Thread(target=self.tr_get_message, args=(upd, context)).start()

    def cb_opt_button(self, upd, context):
        Thread(target=self.tr_opt_button, args=(upd, context)).start()

    def cb_get_document(self, upd, context):
        Thread(target=self.load_history_from_chat_message,
               args=(upd, context)).start()

    # =============================================================================
    # Additional telegram actions
    def send_welcome_message(self, update: Update, context: CallbackContext):
        chat_id = update.effective_chat.id
        self.init_user(chat_id)
        send_text = self.message_template_generator("char_loaded", chat_id)
        context.bot.send_message(
            text=send_text, chat_id=chat_id, reply_markup=self.button_start)

    def last_message_markup_clean(self, context: CallbackContext, chat_id: int):
        if chat_id in self.users and len(self.users[chat_id]["msg_id"]) > 0:
            last_msg = self.users[chat_id]["msg_id"][-1]
            try:
                context.bot.editMessageReplyMarkup(
                    chat_id=chat_id, message_id=last_msg, reply_markup=None)
            except Exception as e:
                print("last_message_markup_clean", e)

    def message_template_generator(self, request: str, chat_id: int, custom_string="") -> str:
        # create a message using default_messages_template or return UNKNOWN_TEMPLATE
        if request in self.default_messages_template and chat_id in self.users:
            msg = self.default_messages_template[request]
            msg = msg.replace("_CHAT_ID_", str(chat_id))
            msg = msg.replace("_NAME1_", self.users[chat_id]["name1"])
            msg = msg.replace("_NAME2_", self.users[chat_id]["name2"])
            msg = msg.replace("_CONTEXT_", self.users[chat_id]["context"])
            msg = msg.replace("_GREETING_", self.users[chat_id]["greeting"])
            msg = msg.replace("_CUSTOM_STRING_", custom_string)
            return msg
        else:
            return self.UNKNOWN_TEMPLATE

    # =============================================================================
    # Work with history! Init/load/save functions
    def load_new_character(self, upd: Update, context: CallbackContext):
        chat_id = upd.message.chat.id
        self.last_message_markup_clean(context, chat_id)
        char_list = self.get_characters_files_list()
        char_file = char_list[int(upd.message.text.split(
            self.load_cmd)[-1].strip().lstrip())]
        self.users[chat_id] = self.load_char_file(char_file=char_file)
        if exists(f'{self.history_dir_path}/{str(chat_id)}{self.users[chat_id]["name2"]}.json'):
            self.load_user_history(chat_id, self.users[chat_id]["name2"])
        send_text = self.message_template_generator("char_loaded", chat_id)
        context.bot.send_message(text=send_text, chat_id=chat_id)

    def get_characters_files_list(self) -> list:
        char_list = []
        for f in listdir(self.characters_dir_path):
            if f.endswith(('.json', '.yaml', '.yml')):
                char_list.append(f)
        return char_list

    def init_user(self, chat_id):
        if chat_id not in self.users:
            # Load default character
            self.users[chat_id] = self.load_char_file(
                char_file=self.default_char_json)
            # Load user history
            user_history_path = f'{self.history_dir_path}/{str(chat_id)}'
            if exists(user_history_path + '.json'):
                self.load_user_history(chat_id, user_history_path + '.json')
            elif self.users[chat_id]['name2'] and exists(user_history_path + self.users[chat_id]['name2'] + '.json'):
                self.load_user_history(
                    chat_id, user_history_path + self.users[chat_id]['name2'] + '.json')

    def load_user_history(self, chat_id, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as user_file:
                data = user_file.read()
            self.users[chat_id] = json.loads(data)
        except Exception as e:
            print(f"load_user_history: {e}")

    def save_user_history(self, chat_id, chat_name=""):
        """
        Save two history file -user+char and default user history files and return their path
        :param chat_id: user chat_id
        :param chat_name: char name (or additional data)
        :return: user_char_file_path, default_user_file_path
        """
        if chat_id not in self.users:
            return None, None

        user_data = json.dumps(self.users[chat_id])
        user_char_file_path = Path(
            f"{self.history_dir_path}/{chat_id}{chat_name}.json")
        with user_char_file_path.open("w", encoding="utf-8") as user_file:
            user_file.write(user_data)

        default_user_file_path = Path(
            f"{self.history_dir_path}/{chat_id}.json")
        with default_user_file_path.open("w", encoding="utf-8") as user_file:
            user_file.write(user_data)

        return str(user_char_file_path), str(default_user_file_path)

    def load_history_from_chat_message(self, upd: Update, context: CallbackContext):
        chat_id = upd.message.chat.id
        default_user_file_path = str(
            Path(f'{self.history_dir_path}/{str(chat_id)}.json'))
        with open(default_user_file_path, 'wb') as f:
            context.bot.get_file(upd.message.document.file_id).download(out=f)

        self.load_user_history(chat_id, default_user_file_path)
        last_message = self.users[chat_id]["history"][-1] if self.users[chat_id]["history"] else "<no message in history>"
        send_text = self.message_template_generator(
            "hist_loaded", chat_id, last_message)
        context.bot.send_message(chat_id=chat_id, text=send_text)

    # =============================================================================
    # Text message handler
    def tr_get_message(self, upd: Update, context: CallbackContext):
        # Extract user input and chat ID
        user_text = upd.message.text
        chat_id = upd.message.chat.id
        # Initialize the user (if necessary)
        self.init_user(chat_id)
        # Send "typing" message
        send_text = self.message_template_generator("typing", chat_id)
        message = context.bot.send_message(
            text=send_text, chat_id=chat_id, parse_mode="HTML")
        # Generate answer and replace "typing" message with it
        answer = self.generate_answer(user_in=user_text, chat_id=chat_id)
        context.bot.editMessageText(
            text=answer, chat_id=chat_id, message_id=message.message_id, reply_markup=self.button)
        # Clear buttons on last message (if they exist in current thread)
        self.last_message_markup_clean(context, chat_id)
        # Add message ID to message history
        self.users[chat_id]["msg_id"].append(message.message_id)
        # Save user history
        self.save_user_history(chat_id, self.users[chat_id]["name2"])
        return True

    # =============================================================================
    # button
    def tr_opt_button(self, upd: Update, context: CallbackContext):
        query = upd.callback_query
        query.answer()
        chat_id = query.message.chat.id
        msg_id = query.message.message_id
        msg_text = query.message.text
        option = query.data
        if chat_id not in self.users:
            self.init_user(chat_id)
        if msg_id not in self.users[chat_id]["msg_id"]:
            send_text = msg_text + \
                self.message_template_generator("mem_lost", chat_id)
            context.bot.editMessageText(
                text=send_text, chat_id=chat_id, message_id=msg_id, reply_markup=None)
        else:
            self.handle_option(option, upd, context, chat_id)
            self.save_user_history(chat_id, self.users[chat_id]["name2"])

    def handle_option(self, option, upd, context, chat_id):
        if option == self.BTN_RESET:
            self.reset_history_button(upd=upd, context=context)
        elif option == self.BTN_CONTINUE:
            self.continue_message_button(upd=upd, context=context)
        elif option == self.BTN_REGEN:
            self.regenerate_message_button(upd=upd, context=context)
        elif option == self.BTN_CUTOFF:
            self.cutoff_message_button(upd=upd, context=context)
        elif option == self.BTN_DOWNLOAD:
            self.send_history_to_chat_button(upd=upd, context=context)
        elif option == self.BTN_CHAR_LIST:
            self.send_characters_list(chat_id, context)

    def send_characters_list(self, chat_id, context):
        char_list = self.get_characters_files_list()
        to_send = []
        for i, char in enumerate(char_list):
            to_send.append(
                f"/{self.load_cmd}{i} {char.replace('.json', '').replace('.yaml', '')}")
            if i % 50 == 0 and i != 0:
                send_text = "\n".join(to_send)
                context.bot.send_message(text=send_text, chat_id=chat_id)
                to_send = []
        if to_send:
            send_text = "\n".join(to_send)
            context.bot.send_message(text=send_text, chat_id=chat_id)

    def continue_message_button(self, upd: Update, context: CallbackContext):
        chat_id = upd.callback_query.message.chat.id

        # send "typing"
        self.last_message_markup_clean(context, chat_id)
        send_text = self.message_template_generator("typing", chat_id)
        message = context.bot.send_message(
            text=send_text, chat_id=chat_id, parse_mode="HTML")

        # get answer and replace message text!
        answer = self.generate_answer(user_in='', chat_id=chat_id)
        context.bot.editMessageText(
            text=answer, chat_id=chat_id, message_id=message.message_id, reply_markup=self.button)
        self.users[chat_id]["msg_id"].append(message.message_id)

    def regenerate_message_button(self, upd: Update, context: CallbackContext):
        chat_id = upd.callback_query.message.chat.id
        msg = upd.callback_query.message
        user = self.users[chat_id]
        # add pretty "retyping" to message text
        send_text = f"{msg.text}{self.message_template_generator('retyping', chat_id)}"
        context.bot.editMessageText(
            text=send_text, chat_id=chat_id, message_id=msg.message_id, parse_mode="HTML")

        # remove last bot answer, read and remove last user reply
        user["history"] = user["history"][:-2]
        user_in = user['user_in'].pop()

        # get answer and replace message text!
        answer = self.generate_answer(user_in=user_in, chat_id=chat_id)
        context.bot.editMessageText(
            text=answer, chat_id=chat_id, message_id=msg.message_id, reply_markup=self.button)

    def cutoff_message_button(self, upd: Update, context: CallbackContext):
        chat_id = upd.callback_query.message.chat.id
        msg = upd.callback_query.message
        if chat_id not in self.users:
            send_text = msg.text + "\n<HISTORY LOST>"
            context.bot.editMessageText(
                text=send_text, chat_id=chat_id, message_id=msg.message_id, reply_markup=self.button)
        else:
            user = self.users[chat_id]
            user_history = user["history"]
            user_msg_ids = user["msg_id"]
            user_in = user["user_in"]
            user_name2 = user["name2"]
            send_text = f"<s>{user_history[-1]}</s>"

            # Edit last message ID (strict lines)
            last_msg_id = user_msg_ids[-1]
            if self.cutoff_mode == self.CUTOFF_STRICT:
                context.bot.editMessageText(
                    text=send_text, chat_id=chat_id, message_id=last_msg_id, parse_mode="HTML")
            else:
                context.bot.deleteMessage(
                    chat_id=chat_id, message_id=last_msg_id)

            # Remove last 2 items from user's history, and last user input
            user_history = user_history[:-2]
            user_in.pop()

            # Remove last message ID
            user_msg_ids.pop()
            # if there is previous message - add buttons to previous message
            if user_msg_ids:
                send_text = user_history[-1]
                message_id = user_msg_ids[-1]
                context.bot.editMessageText(text=send_text, chat_id=chat_id,
                                            message_id=message_id, reply_markup=self.button)
            self.save_user_history(chat_id, user_name2)

    def send_history_to_chat_button(self, upd: Update, context: CallbackContext):
        chat_id = upd.callback_query.message.chat.id

        if chat_id not in self.users:
            return

        default_user_file_path, _ = self.save_user_history(chat_id)
        with open(default_user_file_path, 'r', encoding='utf-8') as default_user_file:
            send_caption = self.message_template_generator(
                "hist_to_chat", chat_id)
            context.bot.send_document(caption=send_caption, document=default_user_file, chat_id=chat_id,
                                      filename=self.users[chat_id]["name2"] + ".json")

    def reset_history_button(self, upd: Update, context: CallbackContext):
        chat_id = upd.callback_query.message.chat.id
        user = self.users[chat_id]

        if chat_id not in self.users:
            return

        if user["msg_id"]:
            self.last_message_markup_clean(context, chat_id)

        user["history"] = []
        user["user_in"] = []
        user["msg_id"] = []

        send_text = self.message_template_generator("mem_reset", chat_id)
        context.bot.send_message(chat_id=chat_id, text=send_text)

    # =============================================================================
    # answer generator
    def generate_answer(self, user_in, chat_id):
        # if generation will fail, return "fail" answer
        answer = self.GENERATOR_FAIL

        # acquire generator lock if we can
        try:
            self.generator_lock.acquire(timeout=600)
            user = self.users[chat_id]

            # Append user_in history
            user["user_in"].append(user_in)

            # Preprocessing: add user_in to history in right order:
            if self.bot_mode == "notebook":
                # If notebook mode - append to history only user_in, no additional preparing;
                user["history"].append(user_in)

            elif user_in.startswith(self.impersonate_prefix):
                # If user_in starts with prefix - impersonate-like (if you try to get "impersonate view")
                # adding "" line to prevent bug in history sequence, user_in is prefix for bot answer
                user["history"].append("")
                user["history"].append(
                    user_in[len(self.impersonate_prefix):] + ":")

            elif user_in == "":
                # if user_in is "" - no user text, it is like continue generation
                # adding "" history line to prevent bug in history sequence, add "name2:" prefix for generation
                user["history"].append("")
                user["history"].append(
                    user["name2"] + ":")

            else:
                # If not notebook/impersonate/continue mode then use ordinary chat preparing
                # add "name1&2:" to user and bot message (generation from name2 point of view);
                user["history"].append(
                    user["name1"] + ":" + user_in)
                user["history"].append(
                    user["name2"] + ":")

            # Set eos_token and stopping_strings.
            stopping_strings = []
            eos_token = None
            if self.bot_mode in ["chat", "chat-restricted"]:
                eos_token = '\n'

            # Make prompt: context + conversation history
            prompt = user["context"] + \
                "\n" + "\n".join(user["history"])

            # Generate!
            generator = generate_reply(question=prompt, state=self.generation_params,
                                       eos_token=eos_token, stopping_strings=stopping_strings)

            # This is "bad" implementation of getting answer
            for a in generator:
                answer = a

            # If generation result zero length - return  "Empty answer."
            if len(answer) < 1:
                answer = self.GENERATOR_EMPTY_ANSWER

        except Exception as e:
            print("generate_answer", e)

        finally:
            # anyway, release generator lock. Then return
            self.generator_lock.release()

            if answer not in [self.GENERATOR_EMPTY_ANSWER, self.GENERATOR_FAIL]:
                # if everything ok - add generated answer in history and return last message
                user["history"][-1] = user["history"][-1] + answer

            return answer

    # =============================================================================
    # load characters char_file from ./characters
    def load_char_file(self, char_file: str):
        # Copy default user data. If reading will fail - return default user data
        user = deepcopy(self.default_users_data.copy())
        try:
            # Try to read char file.
            char_file_path = Path(f'{self.characters_dir_path}/{char_file}')
            with open(char_file_path, 'r', encoding='utf-8') as user_file:
                if char_file.split(".")[-1] == "json":
                    data = json.loads(user_file.read())
                else:
                    data = yaml.safe_load(user_file.read())
            #  load persona and scenario
            if 'you_name' in data:
                user["name1"] = data['you_name']
            if 'char_name' in data:
                user["name2"] = data['char_name']
            if 'name' in data:
                user["name2"] = data['name']
            if 'char_persona' in data:
                user["context"] += f"{data['char_name']}'s Persona: {data['char_persona'].strip()}\n"
            if 'world_scenario' in data:
                user["context"] += f"Scenario: {data['world_scenario'].strip()}\n"
            #  add dialogue examples
            if 'example_dialogue' in data:
                user["context"] += f"{data['example_dialogue'].strip()}\n"
            #  add <START>, add char greeting
            user["context"] += f"{user['context'].strip()}\n<START>\n"
            if 'char_greeting' in data:
                user["context"] += '\n' + data['char_greeting'].strip()
                user["greeting"] = data['char_greeting'].strip()
            if 'greeting' in data:
                user["context"] += '\n' + data['greeting'].strip()
                user["greeting"] = data['greeting'].strip()
            user["context"] = self.replace_template_in_context(
                user["context"], user)
            user["greeting"] = self.replace_template_in_context(
                user["greeting"], user)
        except Exception as e:
            print("load_char_json_file", e)
        finally:
            return user

    @staticmethod
    def replace_template_in_context(s: str, user: dict) -> str:
        s = s.replace('{{char}}', user["name2"])
        s = s.replace('{{user}}', user["name1"])
        s = s.replace('<BOT>', user["name2"])
        s = s.replace('<USER>', user["name1"])
        return s


def run_server():
    # example with char load context:
    tg_server = TelegramBotWrapper(bot_mode=params['bot_mode'], default_char_json=params['character_to_load'],
                                   cutoff_mode=params["cutoff_mode"])
    # by default - read token from extensions/telegram_bot/telegram_token.txt
    tg_server.run_telegram_bot()


def setup():
    Thread(target=run_server, daemon=True).start()