from django.test import TestCase
from django.urls import reverse
from .models import Qasida, Tag, Suggestion

class QasidaModelTest(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(name="spiritual")
        self.qasida = Qasida.objects.create(
            title="Test Qasida",
            author="Test Author",
            language="English",
            lyrics="These are the test lyrics."
        )
        self.qasida.tags.add(self.tag)
        
    def test_qasida_creation(self):
        self.assertEqual(self.qasida.title, "Test Qasida")
        self.assertEqual(self.qasida.tags.count(), 1)
        self.assertEqual(self.qasida.tags.first().name, "spiritual")

    def test_suggestion_creation(self):
        suggestion = Suggestion.objects.create(
            qasida=self.qasida,
            email="test@example.com",
            suggested_lyrics="New lyrics",
            suggested_tags="newtag"
        )
        self.assertEqual(suggestion.qasida, self.qasida)
        self.assertFalse(suggestion.is_approved)

class QasidaViewsTest(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(name="urdu")
        self.qasida = Qasida.objects.create(
            title="Searchable Naat",
            author="Known Author",
            language="Urdu",
            lyrics="Searchable content inside lyrics"
        )
        self.qasida.tags.add(self.tag)

    def test_home_view(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Searchable Naat")

    def test_search_view_by_lyrics(self):
        response = self.client.get(reverse('search'), {'q': 'content'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Searchable Naat")
        
    def test_search_view_by_tag(self):
        response = self.client.get(reverse('search'), {'tag': 'urdu'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Searchable Naat")
        
    def test_search_view_no_results(self):
        response = self.client.get(reverse('search'), {'q': 'nonexistent'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No results found")

    def test_detail_view(self):
        response = self.client.get(reverse('qasida_detail', args=[self.qasida.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Searchable Naat")
        self.assertContains(response, "Searchable content inside lyrics")

    def test_post_suggestion(self):
        response = self.client.post(reverse('qasida_detail', args=[self.qasida.id]), {
            'email': 'user@test.com',
            'suggested_lyrics': 'Better lyrics',
            'suggested_tags': 'good, test'
        })
        self.assertEqual(response.status_code, 302) # redirect on success
        self.assertEqual(Suggestion.objects.count(), 1)
        
        suggestion = Suggestion.objects.first()
        self.assertEqual(suggestion.email, 'user@test.com')
