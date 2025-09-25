# Importations des librairies nécessaires
import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput, ChannelSelect, RoleSelect
import datetime
import asyncio
import os
import json
import pytz
import random
import math

# Importation et configuration de Flask pour l'hébergement sur Render
from flask import Flask
from threading import Thread

# Configuration du bot Discord
intents = discord.Intents.all()
BOT_PREFIX = "!"
NEON_PURPLE = 0x6441a5
NEON_BLUE = 0x027afa
USER_TIMEZONE = pytz.timezone('Europe/Paris')
SERVER_TIMEZONE = pytz.utc
DATABASE_FILE = 'events_contests.json'

def load_data():
    """
    Charge les données des événements et concours depuis un fichier JSON.
    Simule une base de données persistante comme Firebase.
    """
    if os.path.exists(DATABASE_FILE):
        with open(DATABASE_FILE, 'r') as f:
            return json.load(f)
    return {"events": {}, "contests": {}, "settings": {"time_offset_seconds": 0}}

def save_data(data):
    """Sauvegarde les données dans le fichier JSON."""
    with open(DATABASE_FILE, 'w') as f:
        json.dump(data, f, indent=4)

db = load_data()

# --- Serveur Flask pour le maintien en vie du bot ---
app = Flask(__name__)

@app.route('/')
def home():
    """Point de terminaison simple pour l'hébergement."""
    return "Poxel Bot is running!"

def run_flask():
    """Démarre le serveur Flask sur un thread séparé."""
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# --- Fonctions utilitaires pour le formatage et la gestion ---
def get_adjusted_time():
    """Renvoie l'heure UTC actuelle ajustée avec le décalage."""
    offset = db['settings'].get('time_offset_seconds', 0)
    return datetime.datetime.now(SERVER_TIMEZONE) + datetime.timedelta(seconds=offset)

def format_time_left(end_time_str):
    """
    Formate le temps restant en jours, heures, minutes et secondes.
    """
    end_time_utc = datetime.datetime.fromisoformat(end_time_str).replace(tzinfo=SERVER_TIMEZONE)
    now_utc = get_adjusted_time()
    delta = end_time_utc - now_utc
    total_seconds = int(delta.total_seconds())
    
    if total_seconds < 0:
        total_seconds = abs(total_seconds)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        if days > 0:
            return f"FINI IL Y A {days} jour(s), {hours} heure(s)"
        if hours > 0:
            return f"FINI IL Y A {hours} heure(s), {minutes} minute(s)"
        if minutes > 0:
            return f"FINI IL Y A {minutes} minute(s), {seconds} seconde(s)"
        return f"FINI IL Y A {seconds} seconde(s)"

    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    if days > 0:
        return f"{days} jour(s), {hours} heure(s)"
    elif hours > 0:
        return f"{hours} heure(s), {minutes} minute(s)"
    elif minutes > 0:
        return f"{minutes} minute(s), {seconds} seconde(s)"
    else:
        return f"{seconds} seconde(s)"

async def update_event_embed(bot, event_name, interaction=None):
    """
    Met à jour l'embed de l'événement avec les informations actuelles.
    """
    if event_name not in db['events']: return
    event = db['events'][event_name]
    announcement_channel_id = event['announcement_channel_id']
    message_id = event['message_id']
    try:
        channel = bot.get_channel(announcement_channel_id)
        if not channel: return
        message = await channel.fetch_message(message_id)

        embed = discord.Embed(
            title=f"NEW EVENT: {event_name}",
            description="Rejoignez-nous pour un événement spécial !",
            color=NEON_PURPLE
        )
        embed.add_field(name="POINT DE RALLIEMENT", value=f"<#{event['waiting_channel_id']}>", inline=True)
        embed.add_field(name="RÔLE ATTRIBUÉ", value=f"<@&{event['role_id']}>", inline=True)
        
        if not event.get('is_started'):
            start_time_utc = datetime.datetime.fromisoformat(event['start_time']).replace(tzinfo=SERVER_TIMEZONE)
            start_time_paris = start_time_utc.astimezone(USER_TIMEZONE)
            embed.add_field(name="DÉBUT PRÉVU", value=f"Le {start_time_paris.strftime('%d/%m/%Y')} à {start_time_paris.strftime('%Hh%M')}", inline=False)
            embed.add_field(name="DÉBUT DANS", value=format_time_left(event['start_time']), inline=False)
        else:
            embed.add_field(name="TEMPS RESTANT", value=format_time_left(event['end_time']), inline=False)
        
        participants_list = "\n".join([f"- **{p['name']}** ({p['pseudo']})" for p in event['participants']])
        if not participants_list: participants_list = "Aucun participant pour le moment."
            
        embed.add_field(
            name=f"PARTICIPANTS ({len(event['participants'])}/{event['max_participants']})",
            value=participants_list,
            inline=False
        )
        embed.set_image(url="https://cdn.lospec.com/gallery/loading-727267.gif") 
        
        view = EventButtonsView(bot, event_name, event)
        await message.edit(embed=embed, view=view)

        if interaction:
            old_participant_count = event.get('last_participant_count', 0)
            new_participant_count = len(event['participants'])
            max_participants = event.get('max_participants', 0)

            if old_participant_count < max_participants and new_participant_count == max_participants:
                await channel.send(f"@everyone ⛔ **INSCRIPTIONS CLOSES !** L'événement **{event_name}** a atteint son nombre maximum de participants.")
            elif old_participant_count == max_participants and new_participant_count < max_participants:
                await channel.send(f"@everyone ✅ **RÉOUVERTURE !** Une place est disponible pour l'événement **{event_name}**.")

            event['last_participant_count'] = new_participant_count
            save_data(db)
    
    except discord.NotFound:
        if event_name in db['events']:
            del db['events'][event_name]
            save_data(db)
    except Exception as e:
        print(f"Erreur lors de la mise à jour de l'embed pour {event_name}: {e}")

async def update_contest_embed(bot, contest_name):
    """Met à jour l'embed du concours."""
    if contest_name not in db['contests']: return
    contest = db['contests'][contest_name]
    announcement_channel_id = contest['announcement_channel_id']
    message_id = contest['message_id']
    
    try:
        channel = bot.get_channel(announcement_channel_id)
        if not channel: return
        message = await channel.fetch_message(message_id)

        embed = discord.Embed(
            title=contest['title'],
            description=contest['description'],
            color=NEON_BLUE
        )
        
        end_date_time = datetime.datetime.fromisoformat(contest['end_time']).replace(tzinfo=SERVER_TIMEZONE)
        end_date_paris = end_date_time.astimezone(USER_TIMEZONE)
        
        participants_list = "\n".join([f"- <@{p['id']}>" for p in contest['participants']])
        if not participants_list: participants_list = "Aucun participant pour le moment."
        
        embed.add_field(name="INSCRITS", value=participants_list, inline=False)
        embed.add_field(name="FIN DU CONCOURS", value=f"Le {end_date_paris.strftime('%d/%m/%Y')} à {end_date_paris.strftime('%H:%M')}", inline=False)
        embed.add_field(name="TEMPS RESTANT", value=format_time_left(contest['end_time']), inline=False)
        
        view = ContestButtonsView(bot, contest_name, contest)
        await message.edit(embed=embed, view=view)

    except discord.NotFound:
        if contest_name in db['contests']:
            del db['contests'][contest_name]
            save_data(db)
    except Exception as e:
        print(f"Erreur lors de la mise à jour de l'embed du {contest_name}: {e}")

# --- Classes de MODALS et VUES (UI) ---

class ParticipantModal(Modal, title="Vérification de votre pseudo"):
    """Fenêtre modale pour que l'utilisateur entre son pseudo de jeu."""
    game_pseudo = TextInput(
        label="Entrez votre pseudo pour le jeu",
        placeholder="Laissez vide si c'est le même que votre pseudo Discord",
        required=False
    )
    def __init__(self, view, event_name):
        super().__init__()
        self.view = view
        self.event_name = event_name

    async def on_submit(self, interaction: discord.Interaction):
        """Ajoute le participant à l'événement et met à jour l'embed."""
        user = interaction.user
        game_pseudo = self.game_pseudo.value
        if not game_pseudo:
            game_pseudo = user.display_name
        
        self.view.event_data['participants'].append({
            "id": user.id,
            "name": user.display_name,
            "pseudo": game_pseudo
        })
        save_data(db)
        
        await update_event_embed(self.view.bot, self.event_name, interaction=interaction)
        await interaction.response.send_message(f"Vous avez été inscrit à l'événement `{self.event_name}` avec le pseudo `{game_pseudo}`.", ephemeral=True)

class EventButtonsView(View):
    """Vue pour les boutons d'inscription aux événements."""
    def __init__(self, bot, event_name, event_data, timeout=None):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.event_name = event_name
        self.event_data = event_data
        self.max_participants = self.event_data.get('max_participants', 10)
        self.current_participants = len(self.event_data.get('participants', []))

        join_button = Button(label="START", style=discord.ButtonStyle.success, emoji="✅")
        join_button.callback = self.on_join_click

        quit_button = Button(label="QUIT", style=discord.ButtonStyle.danger, emoji="❌")
        quit_button.callback = self.on_quit_click

        if self.current_participants >= self.max_participants:
            join_button.label = "INSCRIPTIONS CLOSES"
            join_button.disabled = True
        
        self.add_item(join_button)
        self.add_item(quit_button)

    async def on_join_click(self, interaction: discord.Interaction):
        """Gère l'inscription d'un utilisateur."""
        user = interaction.user
        if user.id in [p['id'] for p in self.event_data['participants']]:
            await interaction.response.send_message("Vous êtes déjà inscrit à cet événement !", ephemeral=True)
            return
        
        modal = ParticipantModal(self, self.event_name)
        await interaction.response.send_modal(modal)

    async def on_quit_click(self, interaction: discord.Interaction):
        """Gère la désinscription d'un utilisateur."""
        user_id = interaction.user.id
        if user_id not in [p['id'] for p in self.event_data['participants']]:
            await interaction.response.send_message("Vous n'êtes pas inscrit à cet événement.", ephemeral=True)
            return
            
        self.event_data['participants'] = [p for p in self.event_data['participants'] if p['id'] != user_id]
        save_data(db)
        
        await update_event_embed(self.bot, self.event_name, interaction=interaction)
        await interaction.response.send_message("Vous vous êtes désinscrit de l'événement.", ephemeral=True)

class ContestButtonsView(View):
    """Vue pour le bouton d'inscription aux concours."""
    def __init__(self, bot, contest_name, contest_data, timeout=None):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.contest_name = contest_name
        self.contest_data = contest_data
        
        start_button = Button(label="START", style=discord.ButtonStyle.success, emoji="✅")
        start_button.callback = self.on_start_click
        self.add_item(start_button)
        
    async def on_start_click(self, interaction: discord.Interaction):
        """Gère l'inscription au concours."""
        user = interaction.user
        if user.id in [p['id'] for p in self.contest_data['participants']]:
            await interaction.response.send_message("Vous êtes déjà inscrit à ce concours !", ephemeral=True)
            return
            
        self.contest_data['participants'].append({"id": user.id, "name": user.display_name})
        save_data(db)
        
        await update_contest_embed(self.bot, self.contest_name)
        await interaction.response.send_message("Vous êtes inscrit au concours !", ephemeral=True)

class ContestConfigModal(Modal, title="Configurer le Concours"):
    end_date_str = TextInput(label="Date de fin (JJ/MM/AAAA)", placeholder="Ex: 31/12/2025")
    end_time_str = TextInput(label="Heure de fin (HHhMM)", placeholder="Ex: 23h59")
    title_input = TextInput(label="Titre du concours")
    description_input = TextInput(label="Description du concours", style=discord.TextStyle.paragraph)

    def __init__(self, bot, channel_id):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            contest_name = self.title_input.value.strip()
            if contest_name in db['contests']:
                await interaction.response.send_message(f"Un concours nommé `{contest_name}` existe déjà.", ephemeral=True, delete_after=10)
                return

            day, month, year = map(int, self.end_date_str.value.split('/'))
            hour, minute = map(int, self.end_time_str.value.split('h'))
            end_time_naive = datetime.datetime(year, month, day, hour, minute)
            end_time_localized = USER_TIMEZONE.localize(end_time_naive)
            end_time_utc = end_time_localized.astimezone(SERVER_TIMEZONE)

            if end_time_utc < get_adjusted_time():
                await interaction.response.send_message("La date et l'heure de fin sont déjà passées.", ephemeral=True, delete_after=10)
                return

        except (ValueError, IndexError):
            await interaction.response.send_message("Format de date ou d'heure invalide. Utilisez 'JJ/MM/AAAA' et 'HHhMM'.", ephemeral=True, delete_after=10)
            return

        announcement_channel = self.bot.get_channel(self.channel_id)
        if not announcement_channel:
            await interaction.response.send_message("Le salon sélectionné est introuvable.", ephemeral=True)
            return

        contest_data = {
            "title": contest_name,
            "description": self.description_input.value,
            "end_time": end_time_utc.isoformat(),
            "participants": [],
            "announcement_channel_id": self.channel_id,
            "message_id": None,
            "is_finished": False
        }
        
        embed = discord.Embed(title=contest_name, description=self.description_input.value, color=NEON_BLUE)
        embed.add_field(name="FIN DU CONCOURS", value=f"Le {end_time_localized.strftime('%d/%m/%Y')} à {end_time_localized.strftime('%H:%M')}", inline=False)
        embed.add_field(name="TEMPS RESTANT", value=format_time_left(contest_data['end_time']), inline=False)
        embed.add_field(name="INSCRITS", value="Aucun participant pour le moment.", inline=False)
        
        view = ContestButtonsView(self.bot, contest_name, contest_data)
        message = await announcement_channel.send(content="@everyone 🏆 **NOUVEAU CONCOURS !**", embed=embed, view=view)
        
        contest_data['message_id'] = message.id
        db['contests'][contest_name] = contest_data
        save_data(db)
        
        await interaction.response.send_message(f"Le concours `{contest_name}` a été créé avec succès !", ephemeral=True, delete_after=10)

class ContestConfigView(View):
    def __init__(self, bot, timeout=180):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.channel_id = None

        self.channel_select = ChannelSelect(
            placeholder="Choisissez le salon pour le concours...",
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1
        )
        self.channel_select.callback = self.channel_select_callback
        self.add_item(self.channel_select)

        self.configure_button = Button(label="Configurer le concours", style=discord.ButtonStyle.primary, emoji="⚙️", disabled=True)
        self.configure_button.callback = self.configure_button_callback
        self.add_item(self.configure_button)

    async def channel_select_callback(self, interaction: discord.Interaction):
        self.channel_id = int(interaction.data["values"][0])
        self.configure_button.disabled = False
        await interaction.response.edit_message(view=self)

    async def configure_button_callback(self, interaction: discord.Interaction):
        modal = ContestConfigModal(self.bot, self.channel_id)
        await interaction.response.send_modal(modal)
        await interaction.original_response().edit(view=None)
        self.stop()

class TirageAdminView(View):
    def __init__(self, contest_name, timeout=None):
        super().__init__(timeout=timeout)
        self.contest_name = contest_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Seuls les administrateurs peuvent effectuer le tirage.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Tirage au sort", style=discord.ButtonStyle.success, emoji="🏆")
    async def raffle_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        result_message = await _do_raffle_logic(interaction.guild, interaction.channel, interaction.user, self.contest_name)
        await interaction.followup.send(result_message, ephemeral=True)

# --- Composants UI pour la création d'événement ---
class AnnounceChannelSelect(ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="1. Choisissez le salon d'annonce...", min_values=1, max_values=1, channel_types=[discord.ChannelType.text], row=0)
    async def callback(self, interaction: discord.Interaction):
        self.view.announcement_channel_id = self.values[0].id
        await interaction.response.defer()
        await self.view.update_confirm_button_state()

class WaitingChannelSelect(ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="2. Choisissez le salon de ralliement...", min_values=1, max_values=1, channel_types=[discord.ChannelType.text, discord.ChannelType.voice], row=1)
    async def callback(self, interaction: discord.Interaction):
        self.view.waiting_channel_id = self.values[0].id
        await interaction.response.defer()
        await self.view.update_confirm_button_state()

class EventRoleSelect(RoleSelect):
    def __init__(self):
        super().__init__(placeholder="3. Choisissez le rôle à attribuer...", min_values=1, max_values=1, row=2)
    async def callback(self, interaction: discord.Interaction):
        self.view.role_id = self.values[0].id
        await interaction.response.defer()
        await self.view.update_confirm_button_state()

class MaxParticipantsModal(Modal, title="Nombre de participants"):
    participants = TextInput(label="Nombre maximum de participants", placeholder="Ex: 25")
    def __init__(self, target_view):
        super().__init__()
        self.target_view = target_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            num = int(self.participants.value)
            if num <= 0:
                await interaction.response.send_message("Le nombre doit être positif.", ephemeral=True); return
            
            self.target_view.max_participants = num
            self.target_view.set_participants_button.label = f"Participants : {num}"
            self.target_view.set_participants_button.style = discord.ButtonStyle.success
            
            await interaction.response.defer()
            await self.target_view.update_confirm_button_state()
        except ValueError:
            await interaction.response.send_message("Veuillez entrer un nombre valide.", ephemeral=True)

class CreateEventViewStep2(View):
    def __init__(self, bot, step1_data):
        super().__init__(timeout=300)
        self.bot = bot
        self.step1_data = step1_data
        self.announcement_channel_id = None
        self.waiting_channel_id = None
        self.role_id = None
        self.max_participants = None
        self.message = None
        self.add_item(AnnounceChannelSelect())
        self.add_item(WaitingChannelSelect())
        self.add_item(EventRoleSelect())

        self.set_participants_button = Button(label="4. Définir le nombre de participants", style=discord.ButtonStyle.secondary, row=3)
        self.set_participants_button.callback = self.set_participants_callback
        self.add_item(self.set_participants_button)

        self.confirm_button = Button(label="Créer l'événement", style=discord.ButtonStyle.primary, row=4, disabled=True)
        self.confirm_button.callback = self.confirm_callback
        self.add_item(self.confirm_button)


    async def update_confirm_button_state(self):
        if all([self.announcement_channel_id, self.waiting_channel_id, self.role_id, self.max_participants is not None]):
            self.confirm_button.disabled = False
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                self.stop()

    async def set_participants_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MaxParticipantsModal(target_view=self))

    async def confirm_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        event_name = self.step1_data['event_name']
        if event_name in db['events']:
            await interaction.followup.send(f"Un événement nommé `{event_name}` existe déjà."); return

        announcement_channel_obj = interaction.guild.get_channel(self.announcement_channel_id)
        waiting_channel_obj = interaction.guild.get_channel(self.waiting_channel_id)
        role_obj = interaction.guild.get_role(self.role_id)
        
        event_data = {
            "start_time": self.step1_data['start_time_utc'].isoformat(),
            "end_time": self.step1_data['end_time_utc'].isoformat(),
            "role_id": self.role_id,
            "announcement_channel_id": self.announcement_channel_id,
            "waiting_channel_id": self.waiting_channel_id,
            "max_participants": self.max_participants,
            "participants": [], "last_participant_count": 0, "is_started": False,
            "message_id": None, "reminded_30m": False
        }

        embed = discord.Embed(title=f"NOUVEL ÉVÉNEMENT : {event_name}", description="Rejoignez-nous pour un événement spécial !", color=NEON_PURPLE)
        embed.add_field(name="POINT DE RALLIEMENT", value=waiting_channel_obj.mention, inline=True)
        embed.add_field(name="RÔLE ATTRIBUÉ", value=role_obj.mention, inline=True)
        start_time_paris = self.step1_data['start_time_utc'].astimezone(USER_TIMEZONE)
        embed.add_field(name="DÉBUT PRÉVU", value=f"Le {start_time_paris.strftime('%d/%m/%Y')} à {start_time_paris.strftime('%Hh%M')}", inline=False)
        embed.add_field(name="DÉBUT DANS", value=format_time_left(event_data['start_time']), inline=False)
        embed.add_field(name=f"PARTICIPANTS (0/{self.max_participants})", value="Aucun participant pour le moment.", inline=False)
        embed.set_image(url="https://i.imgur.com/uCgE04g.gif")

        view = EventButtonsView(self.bot, event_name, event_data)
        message = await announcement_channel_obj.send(content="@everyone", embed=embed, view=view)

        event_data['message_id'] = message.id
        db['events'][event_name] = event_data
        save_data(db)

        await self.message.delete()
        await interaction.followup.send(f"L'événement `{event_name}` a été créé avec succès !")

class CreateEventModalStep1(Modal):
    event_name = TextInput(label="Nom de l'événement", placeholder="Ex: Soirée Among Us")
    start_time = TextInput(label="Heure de début (HHhMM)", placeholder="Ex: 21h30")
    duration = TextInput(label="Durée", placeholder="Ex: 2h ou 90min")

    def __init__(self, bot, is_planned: bool):
        self.is_planned = is_planned
        title = "Configurer un événement (1/2)"
        super().__init__(title=title)
        self.bot = bot
        if self.is_planned:
            self.date = TextInput(label="Date (JJ/MM/AAAA)", placeholder="Ex: 31/12/2025")
            self.add_item(self.date)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_hour, start_minute = map(int, self.start_time.value.split('h'))
            duration_str = self.duration.value.lower()
            duration_value = int(''.join(filter(str.isdigit, duration_str)))
            if 'min' in duration_str:
                duration_delta = datetime.timedelta(minutes=duration_value)
            elif 'h' in duration_str:
                duration_delta = datetime.timedelta(hours=duration_value)
            else:
                raise ValueError("Format de durée invalide")

            if self.is_planned:
                day, month, year = map(int, self.date.value.split('/'))
                start_time_naive = datetime.datetime(year, month, day, start_hour, start_minute)
                start_time_paris = USER_TIMEZONE.localize(start_time_naive)
                if start_time_paris < datetime.datetime.now(USER_TIMEZONE):
                    await interaction.response.send_message("La date et l'heure sont déjà passées.", ephemeral=True); return
            else:
                now_paris = datetime.datetime.now(USER_TIMEZONE)
                start_time_paris = now_paris.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
                if start_time_paris < now_paris:
                    start_time_paris += datetime.timedelta(days=1)

            start_time_utc = start_time_paris.astimezone(SERVER_TIMEZONE)
            end_time_utc = start_time_utc + duration_delta

            step1_data = {
                "event_name": self.event_name.value.strip(),
                "start_time_utc": start_time_utc,
                "end_time_utc": end_time_utc
            }
            view = CreateEventViewStep2(self.bot, step1_data)
            await interaction.response.send_message("Étape 2/2: Veuillez finaliser la configuration ci-dessous.", view=view, ephemeral=True)
            message = await interaction.original_response()
            view.message = message

        except (ValueError, IndexError):
            await interaction.response.send_message("Format invalide pour la date, l'heure ou la durée.", ephemeral=True)
            return

class CreateEventConfigView(View):
    def __init__(self, bot, is_planned: bool, timeout=180):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.is_planned = is_planned

    @discord.ui.button(label="Configurer", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def configure_button(self, interaction: discord.Interaction, button: Button):
        modal = CreateEventModalStep1(self.bot, self.is_planned)
        await interaction.response.send_modal(modal)
        
# --- Initialisation du bot ---
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents, help_command=None)

@bot.event
async def on_command(ctx):
    """Supprime le message de commande après son exécution."""
    if ctx.guild:
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            print("Le bot n'a pas la permission de supprimer des messages.")
        except discord.NotFound:
            pass

@bot.event
async def on_ready():
    """Événement déclenché quand le bot est prêt."""
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    print("------")
    print(f"Heure actuelle du serveur (UTC) : {datetime.datetime.now(SERVER_TIMEZONE)}")
    print(f"Heure ajustée pour le bot (UTC) : {get_adjusted_time()}")
    check_events.start()
    check_contests.start()

# --- Commandes du bot ---

@bot.command(name="create_event")
@commands.has_permissions(administrator=True)
async def create_event(ctx):
    """Lance la configuration interactive d'un événement pour le jour même."""
    view = CreateEventConfigView(bot, is_planned=False)
    await ctx.send("Cliquez pour configurer un événement pour aujourd'hui.", view=view, ephemeral=True, delete_after=180)

@bot.command(name="create_event_plan")
@commands.has_permissions(administrator=True)
async def create_event_plan(ctx):
    """Lance la configuration interactive d'un événement planifié."""
    view = CreateEventConfigView(bot, is_planned=True)
    await ctx.send("Cliquez pour configurer un événement planifié.", view=view, ephemeral=True, delete_after=180)

@bot.command(name="concours")
@commands.has_permissions(administrator=True)
async def concours(ctx):
    """Lance le processus de création d'un concours via une interface."""
    view = ContestConfigView(bot)
    await ctx.send("Veuillez choisir un salon pour le concours.", view=view, ephemeral=True, delete_after=180)

async def _do_raffle_logic(guild, channel, admin, contest_name):
    """Logique de base pour effectuer un tirage au sort."""
    if contest_name not in db['contests']:
        return f"Le concours `{contest_name}` n'existe pas."
    
    contest_data = db['contests'][contest_name]
    participants = contest_data['participants']
    
    if not participants:
        return f"Il n'y a pas de participants pour le tirage au sort du concours `{contest_name}`."

    winner_data = random.choice(participants)
    winner_id = winner_data['id']
    winner_member = guild.get_member(winner_id)
    
    await channel.send(f"@everyone 🎉 **Félicitations à <@{winner_id}>** ! 🎉\nVous êtes le grand gagnant du tirage au sort pour le concours **{contest_name}** !")
    
    try:
        await admin.send(f"**TIRAGE AU SORT TERMINÉ**\nLe concours **{contest_name}** a désigné <@{winner_id}> comme gagnant.")
    except discord.Forbidden:
        await channel.send("Impossible d'envoyer la notification privée à l'administrateur.", delete_after=120)
    
    if winner_member:
        try:
            embed_dm = discord.Embed(title="🏆VOUS AVEZ GAGNÉ UN CONCOURS !", description=f"Félicitations ! Vous avez gagné le concours **{contest_name}** !\nContactez l'administration pour réclamer votre prix.", color=NEON_BLUE)
            await winner_member.send(embed=embed_dm)
        except discord.Forbidden:
            print(f"Impossible d'envoyer un MP au gagnant {winner_member.name}.")

    try:
        message = await channel.fetch_message(contest_data['message_id'])
        await message.edit(view=None)
    except discord.NotFound: pass
    
    del db['contests'][contest_name]
    save_data(db)
    return f"Tirage au sort pour `{contest_name}` effectué avec succès."

@bot.command(name="tirage")
@commands.has_permissions(administrator=True)
async def tirage(ctx, *, contest_name: str):
    """Effectue un tirage au sort pour un concours."""
    result_message = await _do_raffle_logic(ctx.guild, ctx.channel, ctx.author, contest_name)
    await ctx.send(result_message, delete_after=120)

@bot.command(name="end_concours")
@commands.has_permissions(administrator=True)
async def end_concours(ctx, contest_name: str, *, reason: str = "Raison non spécifiée"):
    """Annule un concours manuellement."""
    if contest_name not in db['contests']:
        await ctx.send(f"Le concours `{contest_name}` n'existe pas.", delete_after=120)
        return
        
    contest_data = db['contests'][contest_name]
    announcement_channel = bot.get_channel(contest_data['announcement_channel_id'])
    
    if announcement_channel and contest_data['message_id']:
        try:
            message = await announcement_channel.fetch_message(contest_data['message_id'])
            embed = message.embeds[0]
            embed.title = f"Concours annulé: {contest_name}"
            embed.description = f"Ce concours a été annulé.\n**Raison:** {reason}"
            embed.clear_fields()
            embed.add_field(name="ÉTAT", value="ANNULÉ", inline=False)
            await message.edit(embed=embed, view=None)
        except discord.NotFound: pass
    
    if announcement_channel:
        await announcement_channel.send(f"@everyone ❌ Le concours **{contest_name}** a été annulé.")
    
    del db['contests'][contest_name]
    save_data(db)
    await ctx.send(f"Le concours `{contest_name}` a été annulé.", delete_after=120)

@bot.command(name="helpoxel", aliases=["help"])
async def help_command(ctx):
    """Affiche toutes les commandes disponibles."""
    embed = discord.Embed(
        title="Guide des commandes Poxel",
        description="Voici la liste des commandes disponibles. Les commandes `(ADMIN)` nécessitent les permissions d'administrateur.",
        color=NEON_PURPLE
    )

    embed.add_field(name="🎉 Commandes d'Événements (ADMIN)", value="---", inline=False)
    embed.add_field(name="`!create_event`", value="Ouvre une fenêtre pour configurer un événement pour le jour même.", inline=False)
    embed.add_field(name="`!create_event_plan`", value="Ouvre une fenêtre pour configurer un événement à une date future.", inline=False)
    
    embed.add_field(name="🏆 Commandes de Concours (ADMIN)", value="---", inline=False)
    embed.add_field(name="`!concours`", value="Ouvre une fenêtre pour configurer et créer un nouveau concours.", inline=False)
    embed.add_field(name="`!end_concours`", value="Annule un concours en cours.\n*Syntaxe:* `!end_concours \"nom_du_concours\" \"raison\"`", inline=False)
    embed.add_field(name="`!tirage`", value="Effectue manuellement le tirage au sort pour un concours terminé.\n*Syntaxe:* `!tirage \"nom_du_concours\"`", inline=False)
    
    embed.add_field(name="🛠️ Commandes Utilitaires", value="---", inline=False)
    embed.add_field(name="`!helpoxel` (ou `!help`)", value="Affiche ce message d'aide.", inline=False)

    await ctx.send(embed=embed, delete_after=120)

# --- Tâches en arrière-plan ---

@tasks.loop(seconds=10)
async def check_events():
    """Vérifie l'état de tous les événements en temps réel."""
    now_utc = get_adjusted_time()
    events_to_delete = []
    for event_name, event_data in list(db['events'].items()):
        try:
            start_time_utc = datetime.datetime.fromisoformat(event_data['start_time']).replace(tzinfo=SERVER_TIMEZONE)
            end_time_utc = datetime.datetime.fromisoformat(event_data['end_time']).replace(tzinfo=SERVER_TIMEZONE)
            channel = bot.get_channel(event_data['announcement_channel_id'])
            if not channel:
                events_to_delete.append(event_name)
                continue
            
            # --- RAPPEL 30 MINUTES AVANT L'ÉVÉNEMENT ---
            if not event_data.get('reminded_30m') and (start_time_utc - now_utc).total_seconds() <= 30 * 60 and start_time_utc > now_utc:
                await channel.send(f"@everyone ⏰ **RAPPEL:** L'événement **{event_name}** commence dans 30 minutes ! N'oubliez pas de vous inscrire.")
                event_data['reminded_30m'] = True
                save_data(db)

            # --- DÉMARRAGE DE L'ÉVÉNEMENT ---
            if not event_data.get('is_started') and now_utc >= start_time_utc:
                if len(event_data['participants']) < 1:
                    await channel.send(f"@everyone ❌ **ANNULATION:** L'événement **{event_name}** est annulé (pas assez de participants).")
                    try:
                        message = await channel.fetch_message(event_data['message_id'])
                        embed = message.embeds[0]
                        embed.title = f"Événement annulé: {event_name}"
                        embed.description = "Annulé (pas de participants)."
                        embed.clear_fields()
                        embed.set_image(url="")
                        await message.edit(embed=embed, view=None)
                    except discord.NotFound: pass
                    events_to_delete.append(event_name)
                    continue

                event_data['is_started'] = True
                save_data(db)

                # Mise à jour de l'embed pour "EN COURS"
                try:
                    message = await channel.fetch_message(event_data['message_id'])
                    embed = discord.Embed(
                        title=f"Événement en cours: {event_name}",
                        description="Cet événement a officiellement commencé. Rendez-vous dans le salon de jeu !",
                        color=NEON_PURPLE
                    )
                    embed.add_field(name="ÉTAT", value="EN COURS", inline=False)
                    participants_list = "\n".join([f"- **{p['name']}**" for p in event_data['participants']])
                    embed.add_field(name=f"PARTICIPANTS ({len(event_data['participants'])})", value=participants_list, inline=False)
                    await message.edit(embed=embed, view=None)
                except Exception as e:
                    print(f"Impossible de mettre à jour le message pour le début de l'événement {event_name}: {e}")

                guild = channel.guild
                role = guild.get_role(event_data['role_id'])
                for p in event_data['participants']:
                    member = guild.get_member(p['id'])
                    if member and role: 
                        await member.add_roles(role)
                        try:
                            await member.send(f"🎉 **L'événement `{event_name}` a démarré !** Le rôle `{role.name}` vous a été attribué. Rendez-vous dans le salon <#{event_data['waiting_channel_id']}>.")
                        except discord.Forbidden:
                            print(f"Impossible d'envoyer un MP à {member.display_name} (DMs bloqués).")

            # --- FIN DE L'ÉVÉNEMENT ---
            elif event_data.get('is_started') and now_utc >= end_time_utc:
                await channel.send(f"@everyone L'événement **{event_name}** est terminé. Merci d'avoir participé ! 🎉")
                
                try:
                    message = await channel.fetch_message(event_data['message_id'])
                    embed = message.embeds[0]
                    embed.title = f"Événement terminé: {event_name}"
                    embed.description = "Cet événement est maintenant terminé. Merci à tous les participants !"
                    embed.clear_fields()
                    embed.add_field(name="ÉTAT", value="TERMINÉ", inline=False)
                    await message.edit(embed=embed, view=None)
                except Exception as e:
                     print(f"Impossible de mettre à jour le message pour la fin de l'événement {event_name}: {e}")

                guild = channel.guild
                role = guild.get_role(event_data['role_id'])
                for p in event_data['participants']:
                    member = guild.get_member(p['id'])
                    if member and role: await member.remove_roles(role)
                events_to_delete.append(event_name)

            # --- MISE À JOUR CONTINUE DU COMPTE À REBOURS ---
            elif not event_data.get('is_started'):
                await update_event_embed(bot, event_name)

        except Exception as e:
            print(f"Erreur en traitant l'événement {event_name}: {e}")
            events_to_delete.append(event_name)

    if events_to_delete:
        for event_name in events_to_delete:
            if event_name in db['events']: del db['events'][event_name]
        save_data(db)

@tasks.loop(seconds=10)
async def check_contests():
    """Vérifie l'état des concours et les termine si nécessaire."""
    now_utc = get_adjusted_time()
    contests_to_delete = []
    for contest_name, contest_data in list(db['contests'].items()):
        end_time_utc = datetime.datetime.fromisoformat(contest_data['end_time']).replace(tzinfo=SERVER_TIMEZONE)

        if now_utc < end_time_utc and not contest_data.get('is_finished'):
            await update_contest_embed(bot, contest_name)

        elif now_utc >= end_time_utc and not contest_data.get('is_finished'):
            channel = bot.get_channel(contest_data['announcement_channel_id'])
            if not channel: continue
            
            try:
                message = await channel.fetch_message(contest_data['message_id'])
                embed = message.embeds[0]
                
                if not contest_data['participants']:
                    embed.title = f"Concours annulé: {contest_name}"
                    embed.description = "Ce concours a été annulé car personne ne s'y est inscrit."
                    embed.clear_fields()
                    embed.add_field(name="INSCRITS", value="Aucun participant", inline=False)
                    embed.add_field(name="FIN DU CONCOURS", value="\u200b", inline=False) # \u200b is a zero-width space to make the field value appear empty
                    await message.edit(embed=embed, view=None)
                    await channel.send(f"@everyone ❌ Le concours **{contest_name}** a été annulé (aucun participant).")
                    contests_to_delete.append(contest_name)
                else:
                    embed.title = f"Concours terminé: {contest_name}"
                    embed.description = "Ce concours est maintenant terminé !"
                    embed.clear_fields()
                    embed.add_field(name="ÉTAT", value="TERMINÉ", inline=False)
                    admin_view = TirageAdminView(contest_name)
                    await message.edit(embed=embed, view=admin_view)
                    await channel.send(f"@everyone Le concours **{contest_name}** est terminé. Le tirage au sort va bientôt avoir lieu.")
                
                contest_data['is_finished'] = True
                save_data(db)
            except discord.NotFound:
                contests_to_delete.append(contest_name)

    if contests_to_delete:
        for contest_name in contests_to_delete:
            if contest_name in db['contests']: del db['contests'][contest_name]
        save_data(db)

if __name__ == "__main__":
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    # Remplacez 'VOTRE_TOKEN_ICI' par le vrai token de votre bot
    bot.run('DISCORD_TOKEN')

