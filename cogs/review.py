# -*- coding: utf-8 -*-
import csv
import math
import os
import asyncio
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from utils.checks import in_review_channel


class Rollback(Exception):
    pass


class ReviewQueue(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        
        self.review_queue = []
        self.in_review = []
        self.review_lock = asyncio.Lock()
        self.messages = []
        self.cols_target = ['insult','severe_toxic','identity_hate','threat','nsfw']
        asyncio.create_task(self.clean_channel())

    
    async def clean_channel(self):
        await self.bot.wait_until_ready()
        # Clear review queue
        channel = self.bot.get_channel(self.bot.config.get('review_channel'))
        await channel.purge(limit=100)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent ):
        
        # Ignore reactions from the bot
        if (payload.user_id == self.bot.user.id): return

        # Ignore reactions not in review
        if not in_review_channel(self, payload.channel_id): return
        
        self.bot.logger.info("Logged reaction")

        channel = self.bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        reaction = [r for r in message.reactions if str(r.emoji) == str(payload.emoji)][0]


        emojis = self.bot.config.get('reaction_emojis')
        min_votes = self.bot.config.get('min_votes')
        # Ignore feature scoring options
        if str(reaction) not in emojis[-4:]: return    
        


        # Complete review when votes reached
        if in_review_channel(self, message) and str(reaction) == emojis[-4] and reaction.count > min_votes:
            self.bot.logger.info("Sending review.")
            async with self.review_lock:
                review_message = next(x for x in self.in_review if x['review'].id == message.id)
                self.in_review.pop(self.in_review.index(review_message))
                reactions = message.reactions
                for r in reactions:
                    if str(r) in emojis[:-4]:
                        i = emojis.index(str(r)) 
                        review_message['score'][self.cols_target[i]] = 1 if (r.count-1) >= math.ceil((reaction.count-1)*2/3) else 0
                
                self.add_train_row(review_message)
                await message.delete()

                # Add new message to queue
                if len(self.review_queue) > 0:
                    new_review = self.review_queue.pop()
                    new_review['review'] = await self.create_new_review(new_review)
                    self.in_review.append(new_review)
        
        # Send to santization queue
        if in_review_channel(self, message) and str(reaction) == emojis[-3]:
            self.bot.logger.info("Sending to santization queue")
            sanitize_cog = self.bot.get_cog('SanitizeQueue')
            if sanitize_cog is None:
                self.bot.logger.info("The cog \"SanitizeQueue\" is not loaded")
                return
            async with self.review_lock:
                review_message = next(x for x in self.in_review if x['review'].id == message.id)
                self.in_review.pop(self.in_review.index(review_message))
                await sanitize_cog.add_to_sanitize_queue(review_message)
                await review_message['review'].delete()
                if len(self.review_queue) > 0:
                    new_review = self.review_queue.pop()
                    new_review['review'] = await self.create_new_review(new_review)
                    self.in_review.append(new_review)
                
        
    def add_train_row(self, row: dict={'message': str, 'score': dict}):
        row = ([row['message']] + [x[1] for x in row['score'].items()])
        is_new_file = not os.path.exists("./input/new_train.csv")
            
        with open(r'./input/new_train.csv', 'a') as f:
            writer = csv.writer(f)
            if is_new_file:
                writer.writerow(['comment_text'] + self.cols_target)
            writer.writerow(row)

    async def create_new_review(self, review: dict={'message': str, 'score': {'insult': int, 'severe_toxic': int, 'identity_hate': int, 'threat': int}}):
        message = review['message']
        scores = review['score']
        score_values = []

        for i, (k, v) in enumerate(scores.items()):
            score_values.append(f"{self.bot.config.get('reaction_emojis')[i]} {round(v,2)}")

        embed = discord.Embed(
            title='Review Message',
            description=message,
            color=0xff0000
        )
        embed.add_field(name='Scores', value='||' + ' '.join(score_values) + '||')
        review_message = await self.bot.get_channel(self.bot.config.get('review_channel')).send(embed=embed)
        
        for emoji in self.bot.config.get('reaction_emojis')[:-2]:
            await review_message.add_reaction(emoji)
        
        return review_message

    async def add_reviews_to_queue(self, new_reviews):
        max_reviews_size = self.bot.config.get('max_reviews_size')
        async with self.review_lock:
            nlp_cog = self.bot.get_cog('NLP')
            if nlp_cog is None:
                self.bot.logger.info("The cog \"NLP\" is not loaded")
                return
            for r in new_reviews:
                r['message'] = nlp_cog.clean_text(r['message'].content if type(r['message']) is not str else r['message'])
            if len(self.review_queue) < self.bot.config.get('queue_length'):
                self.review_queue = new_reviews + self.review_queue
            if len(self.in_review) < max_reviews_size and len(self.review_queue) > 0:    
                for i in range(min(max_reviews_size-len(self.in_review),len(self.review_queue))):
                    new_review = self.review_queue.pop()
                    new_review['review'] = await self.create_new_review(new_review)
                    self.in_review.append(new_review)
    
    
            
def setup(bot):
    bot.add_cog(ReviewQueue(bot))

